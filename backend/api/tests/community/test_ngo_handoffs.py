"""NGOs, surplus offers, donated meals and hand-offs (M9 §3.5): who sees and writes what, the
offer consumer, first-claim-wins (also under a real race), each kind of hand-off with its refusals,
the confirmation, meal pledges served oldest first, drives with an NGO, the impact facts, and that
an NGO coordinator never sees a donor."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.community import drives, handoffs, ngos
from gharbasket.modules.community.models import (
    Donation,
    HandoffMeal,
    MealPledge,
    Ngo,
    NgoHandoff,
    NgoOffer,
)
from gharbasket.modules.hub_ops import receiving, service, surplus
from gharbasket.modules.hub_ops.models import (
    Hub,
    InventoryLot,
    StockMove,
    SurplusOffer,
    WasteLog,
)
from gharbasket.modules.hub_ops.schemas import HubIn
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.files import create_store
from gharbasket.platform.models import AuditEntry, OutboxEvent, Upload
from gharbasket.platform.outbox import publish
from gharbasket.tools.seed import SEED_HUB_ID, SEED_NGO_ID

from ..hub_ops.conftest import hub_body
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from ..test_files import SETTINGS as STORAGE_SETTINGS
from ..test_files import FakeS3
from .conftest import deliver

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("donation_world")]

PRIYA = "+919800001001"  # Sunrise Towers, flat A-1203
SUNITA = "+919800001002"  # Green Valley Heights
VIKRAM = "+919800001008"  # hub manager of the seed hub
ASHA = "+919800001010"  # ngo_coordinator of SEED_NGO_ID
NEHA = "+919800001011"  # admin
OTHER_HUB = uuid.UUID("0199a000-0000-7000-8000-00000000a0ff")
DONOR_TRACES = ("household", "flat", "A-1203", "tower", "Priya", "Sunita", "+91980000100")


async def _as(api: Any, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def _user(db: AsyncSession, phone: str) -> uuid.UUID:
    user, _ = await find_or_create_user(db, phone)
    return user.id


async def _with_role(
    api: Any, google: FakeGoogle, db: AsyncSession, phone: str, role: str, scope: uuid.UUID
) -> dict[str, str]:
    """Headers of a new person holding only `role:<scope>` (X-Active-Role)."""
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    user = await db.get(User, uuid.UUID(signed["user"]["id"]))
    assert user is not None
    db.add(UserRole(user_id=user.id, role=role, scope_type=ROLE_SCOPES[role], scope_id=scope))
    await db.commit()
    refreshed = await api.post("/v1/auth/refresh", json={"refresh_token": signed["refresh_token"]})
    return {**bearer(refreshed.json()["access_token"]), "X-Active-Role": f"{role}:{scope}"}


ROLE_SCOPES = {"hub_manager": "hub", "ngo_coordinator": "ngo"}


def _ngo(ngo_id: uuid.UUID = SEED_NGO_ID, **fields: Any) -> Ngo:
    values: dict[str, Any] = {
        "name": "Annapurna Food Bank",
        "kind": "food_bank",
        "hub_ids": [SEED_HUB_ID],
        "accepts": ["items", "food", "meals"],
        "verified": True,
        **fields,
    }
    return Ngo(id=ngo_id, **values)


def _key(name: str) -> dict[str, str]:
    return {"Idempotency-Key": name}


def _storage() -> tuple[Any, FakeS3]:
    s3 = FakeS3()
    store = create_store(STORAGE_SETTINGS)
    assert store is not None
    store.client = s3
    return store, s3


async def _photo(db: AsyncSession, s3: FakeS3, user_id: uuid.UUID, purpose: str = "handoff_photo"):
    key = f"{purpose}/{user_id}/{uuid.uuid4()}.jpg"
    db.add(
        Upload(key=key, user_id=user_id, purpose=purpose, content_type="image/jpeg", size_bytes=9)
    )
    s3.objects[key] = ("image/jpeg", 9)
    await db.flush()
    return key


async def _verified(db: AsyncSession, phone: str, *, hub_id: uuid.UUID = SEED_HUB_ID) -> Donation:
    """A donation the hub already counted: 2.5 kg clothes and 2 toys."""
    user_id = await _user(db, phone)
    household = await identity_api.household_for_user(db, user_id)
    assert household is not None
    donation = Donation(
        user_id=user_id,
        household_id=household.id,
        hub_id=hub_id,
        society_id=household.society_id,
        items=[
            {"code": "clothes", "unit": "kg", "qty": 3, "counted": 2.5, "rate": 40, "points": 100},
            {"code": "toys", "unit": "each", "qty": 2, "counted": 2, "rate": 15, "points": 30},
        ],
        status="verified",
        pickup_on=date(2026, 10, 13),
        estimated_points=150,
        points_awarded=130,
        verified_at=datetime.now(UTC),
    )
    db.add(donation)
    await db.flush()
    return donation


async def _ngo_offer(db: AsyncSession, hub_id: uuid.UUID = SEED_HUB_ID, qty: int = 4) -> uuid.UUID:
    """A grade C lot offered to kitchens, moved on unclaimed to an NGO (M6's cascade): the NGO
    offer's id. Its `surplus_offered` is published, not yet delivered."""
    at = datetime.now(UTC)
    lot = InventoryLot(
        hub_id=hub_id,
        product_id=uuid.uuid4(),
        unit="kg",
        source="receipt",
        grade="C",
        qty_in=Decimal(qty),
        qty_on_hand=Decimal(0),
        received_at=at - timedelta(hours=8),
        best_before=at + timedelta(days=10),
    )
    db.add(lot)
    await db.flush()
    await receiving.move(
        db,
        hub_id=hub_id,
        product_id=lot.product_id,
        unit="kg",
        kind="receive",
        ref_id=uuid.uuid4(),
        qty=Decimal(qty),
        lot=lot,
    )
    kitchen = (await surplus.move_on(db, lot, lot.qty_on_hand, at, offer=None)).offers[0]
    kitchen.status = "expired"
    return (await surplus.move_on(db, lot, kitchen.qty, at, offer=kitchen)).offers[0].id


async def _payloads(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


def _private(answer: Any) -> None:
    """An NGO coordinator's answer names no donor."""
    text = json.dumps(answer.json())
    for trace in DONOR_TRACES:
        assert trace not in text, trace


# --- NGOs -----------------------------------------------------------------------------------------


async def test_admins_write_ngos_and_each_role_sees_its_own(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    db_session.add_all([_ngo(), _ngo(uuid.uuid4(), name="Far Away Trust", hub_ids=[OTHER_HUB])])
    await db_session.commit()
    api = clients()
    neha, vikram = await _as(api, google, NEHA), await _as(api, google, VIKRAM)
    asha, priya = await _as(api, google, ASHA), await _as(api, google, PRIYA)
    body = {
        "name": "Little Steps School",
        "kind": "school",
        "hub_ids": [str(SEED_HUB_ID)],
        "accepts": ["items"],
        "registration_ref": "DARPAN MH/2019/0123456",
    }

    assert (await api.post("/v1/admin/ngos", json=body, headers={**vikram, **_key("v")})).json()[
        "code"
    ] == "auth.forbidden"
    unknown = {**body, "hub_ids": [str(OTHER_HUB)]}
    refused = await api.post("/v1/admin/ngos", json=unknown, headers={**neha, **_key("u")})
    assert (refused.status_code, refused.json()["code"]) == (422, "request.invalid")
    created = await api.post("/v1/admin/ngos", json=body, headers={**neha, **_key("n1")})
    assert created.status_code == 201, created.text
    school = created.json()
    assert (school["verified"], school["status"], school["coordinator"], school["version"]) == (
        False,
        "active",
        None,
        1,
    )
    assert created.headers["ETag"] == '"1"'
    replay = await api.post("/v1/admin/ngos", json=body, headers={**neha, **_key("n1")})
    assert replay.json() == school

    url = f"/v1/admin/ngos/{school['id']}"
    assert (await api.patch(url, json={"verified": True}, headers=neha)).status_code == 428
    stale = await api.patch(url, json={"verified": True}, headers={**neha, "If-Match": '"9"'})
    assert stale.json()["code"] == "request.version_mismatch"
    nulled = await api.patch(url, json={"status": None}, headers={**neha, "If-Match": '"1"'})
    assert nulled.status_code == 422
    patched = await api.patch(
        url,
        json={"verified": True, "note": "Collects Tuesdays"},
        headers={**neha, "If-Match": '"1"'},
    )
    assert patched.status_code == 200, patched.text
    assert (patched.json()["verified"], patched.json()["version"]) == (True, 2)
    gone = await api.patch(
        f"/v1/admin/ngos/{uuid.uuid4()}",
        json={"verified": True},
        headers={**neha, "If-Match": '"1"'},
    )
    assert gone.json()["code"] == "community.ngo_not_found"
    audited = await db_session.execute(
        select(AuditEntry.action).where(AuditEntry.entity_id == school["id"])
    )
    assert sorted(audited.scalars()) == ["community.ngo_created", "community.ngo_updated"]

    def names(answer: Any) -> list[str]:
        assert answer.status_code == 200, answer.text
        return [n["name"] for n in answer.json()["items"]]

    assert names(await api.get("/v1/ngos", headers=neha)) == [
        "Annapurna Food Bank",
        "Far Away Trust",
        "Little Steps School",
    ]
    assert names(await api.get("/v1/ngos", headers=vikram)) == [
        "Annapurna Food Bank",
        "Little Steps School",
    ]
    own = await api.get("/v1/ngos", headers=asha)
    assert names(own) == ["Annapurna Food Bank"]
    assert own.json()["items"][0]["coordinator"] == {
        "name": "Asha Fernandes",
        "phone_e164": ASHA,
    }
    elsewhere = await api.get(f"/v1/ngos?hub_id={OTHER_HUB}", headers=vikram)
    assert elsewhere.json()["code"] == "auth.forbidden"
    assert (await api.get("/v1/ngos", headers=priya)).status_code == 403


# --- surplus offers -------------------------------------------------------------------------------


async def test_the_consumer_copies_ngo_offers_once_for_an_eligible_ngo(
    db_session: AsyncSession,
) -> None:
    unserved = await _ngo_offer(db_session)
    await deliver(db_session)  # no NGO yet: the hub manager settles it by phone
    assert await db_session.get(NgoOffer, unserved) is None

    db_session.add(_ngo(accepts=["items"]))  # takes no food
    paused = _ngo(uuid.uuid4(), name="Paused", status="paused")
    db_session.add(paused)
    await db_session.flush()
    await _ngo_offer(db_session)
    await deliver(db_session)
    assert (await db_session.execute(select(NgoOffer))).first() is None

    paused.status = "active"
    offered = await _ngo_offer(db_session, qty=6)
    await deliver(db_session)
    copy = await db_session.get(NgoOffer, offered)
    assert copy is not None
    m6 = await db_session.get(SurplusOffer, offered)
    assert m6 is not None
    assert (copy.hub_id, copy.qty, copy.unit, copy.kg_tenths, copy.status, copy.expires_at) == (
        SEED_HUB_ID,
        6,
        "kg",
        60,
        "open",
        m6.expires_at,
    )
    [event] = [
        e
        for e in (await db_session.execute(select(OutboxEvent))).scalars()
        if e.payload.get("offer_id") == str(offered)
    ]
    await ngos.on_surplus_offered(db_session, event)  # redelivered: still one copy
    for bad in ({**event.payload, "qty": "lots"}, {**event.payload, "offer_id": None}, {}):
        await ngos.on_surplus_offered(
            db_session,
            OutboxEvent(event_type=event.event_type, payload={**bad, "offered_to": "ngo"}),
        )
    rows = (await db_session.execute(select(NgoOffer.offer_id))).scalars().all()
    assert rows == [offered]
    assert ngos.weigh_tenths(Decimal(3), "piece", 250.0) == 8  # 0.75 kg, half up
    assert ngos.weigh_tenths(Decimal(3), "pack", None) is None


async def test_offers_by_role_and_the_first_claim_wins(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    roti_id = uuid.uuid4()
    db_session.add_all([_ngo(), _ngo(roti_id, name="Roti Bank")])
    await db_session.flush()
    offer_ids = [await _ngo_offer(db_session) for _ in range(3)]
    first, second, settled = offer_ids
    await deliver(db_session)
    await db_session.commit()
    api = clients()
    asha, vikram = await _as(api, google, ASHA), await _as(api, google, VIKRAM)
    neha, priya = await _as(api, google, NEHA), await _as(api, google, PRIYA)
    roti = await _with_role(api, google, db_session, "+919800000950", "ngo_coordinator", roti_id)
    stranger = await _with_role(api, google, db_session, "+919800000951", "hub_manager", OTHER_HUB)

    def ids(answer: Any) -> set[str]:
        assert answer.status_code == 200, answer.text
        return {o["offer_id"] for o in answer.json()["items"]}

    everything = {str(i) for i in offer_ids} | {
        str(o) for o in (await db_session.execute(select(NgoOffer.offer_id))).scalars()
    }
    assert ids(await api.get("/v1/ngo/offers", headers=asha)) == everything
    assert ids(await api.get("/v1/ngo/offers", headers=vikram)) == everything
    assert ids(await api.get("/v1/ngo/offers", headers=neha)) == everything
    assert ids(await api.get("/v1/ngo/offers", headers=stranger)) == set()
    assert (
        await api.get(f"/v1/ngo/offers?hub_id={SEED_HUB_ID}", headers=stranger)
    ).status_code == 403
    assert (await api.get("/v1/ngo/offers", headers=priya)).status_code == 403

    def claim(offer_id: uuid.UUID, who: dict[str, str], ngo_id: uuid.UUID, key: str) -> Any:
        return api.post(
            f"/v1/ngo/offers/{offer_id}/claim",
            json={"ngo_id": str(ngo_id)},
            headers={**who, **_key(key)},
        )

    assert (await claim(first, vikram, SEED_NGO_ID, "v")).json()["code"] == "auth.forbidden"
    assert (await claim(first, asha, roti_id, "x")).json()["code"] == "community.ngo_not_found"
    missing = await claim(uuid.uuid4(), asha, SEED_NGO_ID, "m")
    assert missing.json()["code"] == "community.offer_not_found"
    won = await claim(first, asha, SEED_NGO_ID, "c1")
    assert won.status_code == 200, won.text
    assert (won.json()["status"], won.json()["claimed_by"]) == (
        "claimed",
        {"id": str(SEED_NGO_ID), "name": "Annapurna Food Bank"},
    )
    assert (await claim(first, asha, SEED_NGO_ID, "c1")).json() == won.json()  # replayed
    taken = await claim(first, roti, roti_id, "r1")
    assert (taken.status_code, taken.json()["code"]) == (409, "community.offer_taken")
    m6 = await db_session.get(SurplusOffer, first, populate_existing=True)
    assert m6 is not None and m6.status == "taken"
    logs = (await db_session.execute(select(WasteLog).where(WasteLog.offer_id == first))).scalars()
    assert [(log.cascade_to, log.taken_by_name) for log in logs] == [("ngo", "Annapurna Food Bank")]

    # Past expires_at, or settled at the hub by phone first: expired.
    copy = await db_session.get(NgoOffer, second)
    assert copy is not None
    copy.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await surplus.settle(db_session, settled, "declined", now=datetime.now(UTC))
    await db_session.commit()
    for offer_id, key in ((second, "e1"), (settled, "e2")):
        late = await claim(offer_id, roti, roti_id, key)
        assert (late.status_code, late.json()["code"]) == (409, "community.offer_expired")
    expired = await api.get("/v1/ngo/offers?status=expired", headers=asha)
    assert str(second) in ids(expired)
    other = await db_session.get(Ngo, roti_id)
    assert other is not None
    other.status = "paused"
    await db_session.commit()
    another = await _ngo_offer(db_session)
    await deliver(db_session)
    await db_session.commit()
    paused = await claim(another, roti, roti_id, "p1")
    assert paused.json()["code"] == "community.ngo_does_not_accept"


# --- the race, on separate connections (these commit; they clean up after themselves) -------------


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, pooled=False)
    yield engine
    await engine.dispose()


async def test_two_concurrent_claims_one_wins_and_settles_once(engine: AsyncEngine) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        hub = await service.create_hub(
            session, HubIn.model_validate(hub_body(f"ngo-race-{uuid.uuid4().hex[:8]}"))
        )
        first, second = _ngo(uuid.uuid4(), hub_ids=[hub.id]), _ngo(uuid.uuid4(), hub_ids=[hub.id])
        session.add_all([first, second])
        await session.flush()
        offer_id = await _ngo_offer(session, hub.id)
        m6 = await session.get(SurplusOffer, offer_id)
        assert m6 is not None
        session.add(
            NgoOffer(
                offer_id=offer_id,
                hub_id=hub.id,
                product_id=m6.product_id,
                qty=m6.qty,
                unit="kg",
                kg_tenths=40,
                expires_at=m6.expires_at,
            )
        )
        await session.commit()
    held = asyncio.Event()

    def scope(ngo: Ngo) -> ngos.Scope:
        return ngos.Scope(False, frozenset(), frozenset({ngo.id}))

    async def winner() -> str:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await ngos.claim(session, scope(first), offer_id, first.id, uuid.uuid4())
            held.set()
            await asyncio.sleep(0.5)  # the other claim waits for the offer's row meanwhile
            await session.commit()
            return "claimed"

    async def loser() -> str:
        await asyncio.wait_for(held.wait(), 10)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                await ngos.claim(session, scope(second), offer_id, second.id, uuid.uuid4())
            except ProblemError as error:
                return error.code
            await session.commit()
            return "claimed"

    async def both_at_once() -> list[str]:
        return list(await asyncio.gather(winner(), loser()))

    try:
        assert await asyncio.wait_for(both_at_once(), 30) == ["claimed", "community.offer_taken"]
        async with AsyncSession(engine) as session:
            copy = await session.get(NgoOffer, offer_id)
            assert copy is not None and copy.claimed_by_ngo_id == first.id
            taken = await session.execute(
                select(WasteLog.taken_by_name).where(
                    WasteLog.offer_id == offer_id, WasteLog.cascade_to == "ngo"
                )
            )
            assert taken.scalars().all() == [first.name]  # settle_surplus ran once
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(delete(NgoOffer).where(NgoOffer.hub_id == hub.id))
            await session.execute(delete(Ngo).where(Ngo.id.in_([first.id, second.id])))
            for model in (WasteLog, SurplusOffer, StockMove, InventoryLot):
                await session.execute(delete(model).where(model.hub_id == hub.id))
            await session.execute(delete(Hub).where(Hub.id == hub.id))
            await session.execute(
                delete(OutboxEvent).where(OutboxEvent.payload["hub_id"].astext == str(hub.id))
            )
            await session.commit()


# --- hand-offs ------------------------------------------------------------------------------------


async def test_items_are_handed_over_once_and_the_hub_waits_for_the_ngo(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    store, s3 = _storage()
    schools_id = uuid.uuid4()
    db_session.add_all([_ngo(), _ngo(schools_id, name="Roti Bank", accepts=["food"])])
    gift, other_gift = await _verified(db_session, PRIYA), await _verified(db_session, SUNITA)
    vikram_id, asha_id = await _user(db_session, VIKRAM), await _user(db_session, ASHA)
    hub_photo, asha_photo = (
        await _photo(db_session, s3, vikram_id),
        await _photo(db_session, s3, asha_id),
    )
    donation_photo = await _photo(db_session, s3, vikram_id, "donation_photo")
    still_waiting = Donation(
        **{
            c: getattr(gift, c)
            for c in ("user_id", "household_id", "hub_id", "society_id", "items")
        },
        status="picked_up",
        pickup_on=gift.pickup_on,
        estimated_points=1,
    )
    db_session.add(still_waiting)
    await db_session.commit()
    gift_id, other_id, waiting_id = gift.id, other_gift.id, still_waiting.id
    api = clients(storage=store)
    vikram, asha = await _as(api, google, VIKRAM), await _as(api, google, ASHA)
    priya = await _as(api, google, PRIYA)
    stranger = await _with_role(api, google, db_session, "+919800000952", "hub_manager", OTHER_HUB)
    body: dict[str, Any] = {
        "ngo_id": str(SEED_NGO_ID),
        "hub_id": str(SEED_HUB_ID),
        "kind": "items",
        "donation_ids": [str(gift_id)],
        "photo_keys": [hub_photo],
        "people_reached": 40,
        "note": "Boys' hostel",
    }

    def record(fields: dict[str, Any], who: dict[str, str], key: str) -> Any:
        return api.post("/v1/ngo-handoffs", json={**body, **fields}, headers={**who, **_key(key)})

    refusals = [
        ({"hub_id": str(OTHER_HUB)}, vikram, "auth.forbidden"),
        ({}, stranger, "auth.forbidden"),
        ({"ngo_id": str(uuid.uuid4())}, vikram, "community.ngo_not_found"),
        ({"ngo_id": str(schools_id)}, vikram, "community.ngo_does_not_accept"),
        ({"donation_ids": []}, vikram, "community.handoff_empty"),
        ({"offer_ids": [str(uuid.uuid4())]}, vikram, "community.handoff_empty"),
        ({"meals": 2}, vikram, "community.handoff_empty"),
        ({"photo_keys": []}, vikram, "community.photo_key_invalid"),
        ({"photo_keys": [asha_photo]}, vikram, "community.photo_key_invalid"),  # not his
        ({"photo_keys": [donation_photo]}, vikram, "community.photo_key_invalid"),
        ({"donation_ids": [str(waiting_id)]}, vikram, "community.donation_not_ready"),
        ({"handed_at": "2099-01-01T00:00:00Z"}, vikram, "request.invalid"),
    ]
    for n, (fields, who, code) in enumerate(refusals):
        refused = await record(fields, who, f"r{n}")
        assert refused.json()["code"] == code, (fields, refused.text)
    neha = await _as(api, google, NEHA)
    for who in (priya, neha):  # admins read hand-offs; only the hub and the NGO record them
        assert (await record({}, who, "p")).status_code == 403

    created = await record({}, vikram, "h1")
    assert created.status_code == 201, created.text
    out = created.json()
    assert (out["recorded_by"], out["confirmed_at"], out["donation_ids"], out["offer_ids"]) == (
        "hub",
        None,
        [str(gift_id)],
        [],
    )
    assert out["summary"] == {"items": 5, "kg": 2.5, "meals": 0}  # 2.5 kg + 2 toys, half up
    assert (out["photos"], len(out["photo_urls"]), out["people_reached"]) == (1, 1, 40)
    assert out["ngo"] == {"id": str(SEED_NGO_ID), "name": "Annapurna Food Bank"}
    claimed = await db_session.get(Upload, hub_photo)
    assert claimed is not None and claimed.claimed_by == "community"
    assert claimed.delete_after is not None
    assert claimed.delete_after > datetime.now(UTC) + timedelta(days=3 * 365)
    await db_session.refresh(gift)
    assert (gift.status, gift.handoff_id) == ("handed_over", uuid.UUID(out["id"]))
    [published] = await _payloads(db_session, "community.handoff_recorded")
    assert published == {
        "handoff_id": out["id"],
        "ngo_id": str(SEED_NGO_ID),
        "hub_id": str(SEED_HUB_ID),
        "kind": "items",
        "items": 5,
        "kg_tenths": 25,
        "meals": 0,
    }
    again = await record({"photo_keys": [await _photo(db_session, s3, vikram_id)]}, vikram, "h2")
    await db_session.commit()
    assert (again.status_code, again.json()["code"], again.json()["donation_ids"]) == (
        409,
        "community.donation_not_ready",
        [str(gift_id)],
    )
    reused = await record({"donation_ids": [str(other_id)]}, vikram, "h3")
    assert reused.json()["code"] == "community.photo_key_invalid"  # one photo, one hand-off
    mine = (await api.get("/v1/donations", headers=priya)).json()["items"]
    assert [(d["status"], d["ngo"]) for d in mine] == [
        ("picked_up", None),
        ("handed_over", {"name": "Annapurna Food Bank"}),
    ]

    # The coordinator confirms the hub's record once; another NGO's coordinator cannot.
    url = f"/v1/ngo-handoffs/{out['id']}/confirm"
    roti = await _with_role(api, google, db_session, "+919800000953", "ngo_coordinator", schools_id)
    assert (await api.post(url, json={}, headers={**roti, **_key("x")})).json()[
        "code"
    ] == "community.handoff_not_found"
    for who in (vikram, neha):
        assert (await api.post(url, json={}, headers={**who, **_key("v")})).status_code == 403
    confirmed = await api.post(
        url, json={"people_reached": 45, "photo_keys": [asha_photo]}, headers={**asha, **_key("c")}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed_at"] is not None
    assert (confirmed.json()["people_reached"], confirmed.json()["photos"]) == (45, 2)
    _private(confirmed)
    twice = await api.post(url, json={}, headers={**asha, **_key("c2")})
    assert (twice.status_code, twice.json()["code"]) == (409, "community.handoff_already_confirmed")

    # Recorded by the NGO itself: confirmed at once.
    own = await record(
        {
            "donation_ids": [str(other_id)],
            "photo_keys": [await _photo(db_session, s3, asha_id)],
        },
        asha,
        "a1",
    )
    await db_session.commit()
    assert own.status_code == 201, own.text
    assert own.json()["recorded_by"] == "ngo" and own.json()["confirmed_at"] is not None
    _private(own)


async def test_food_hand_offs_name_offers_this_ngo_claimed(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    store, s3 = _storage()
    roti_id = uuid.uuid4()
    db_session.add_all([_ngo(), _ngo(roti_id, name="Roti Bank")])
    await db_session.flush()
    mine, theirs, unclaimed = [await _ngo_offer(db_session, qty=q) for q in (5, 3, 2)]
    await deliver(db_session)
    asha_id = await _user(db_session, ASHA)
    photos = [await _photo(db_session, s3, asha_id) for _ in range(3)]
    await db_session.commit()
    api = clients(storage=store)
    asha = await _as(api, google, ASHA)
    roti_staff = await _with_role(
        api, google, db_session, "+919800000954", "ngo_coordinator", roti_id
    )
    for offer_id, who, ngo_id in ((mine, asha, SEED_NGO_ID), (theirs, roti_staff, roti_id)):
        claimed = await api.post(
            f"/v1/ngo/offers/{offer_id}/claim",
            json={"ngo_id": str(ngo_id)},
            headers={**who, **_key(f"c{offer_id}")},
        )
        assert claimed.status_code == 200, claimed.text

    ready = await api.get(f"/v1/ngo-handoffs/ready?hub_id={SEED_HUB_ID}", headers=asha)
    assert ready.status_code == 200, ready.text
    assert [o["offer_id"] for o in ready.json()["offers"]] == [str(mine)]  # her NGO's only

    def food(offer_ids: list[uuid.UUID], photo: str, key: str) -> Any:
        body = {
            "ngo_id": str(SEED_NGO_ID),
            "hub_id": str(SEED_HUB_ID),
            "kind": "food",
            "offer_ids": [str(i) for i in offer_ids],
            "photo_keys": [photo],
        }
        return api.post("/v1/ngo-handoffs", json=body, headers={**asha, **_key(key)})

    unknown = uuid.uuid4()
    for n, offer_ids in enumerate(([theirs], [unclaimed], [mine, unknown])):
        refused = await food(offer_ids, photos[0], f"r{n}")
        assert refused.json()["code"] == "community.offer_not_ready", refused.text
        assert refused.json()["offer_ids"] == [str(offer_ids[-1])]
    given = await food([mine], photos[1], "f1")
    assert given.status_code == 201, given.text
    assert (given.json()["offer_ids"], given.json()["summary"]) == (
        [str(mine)],
        {"items": 0, "kg": 5.0, "meals": 0},
    )
    again = await food([mine], photos[2], "f2")
    assert again.json()["code"] == "community.offer_not_ready"
    ready = await api.get(f"/v1/ngo-handoffs/ready?hub_id={SEED_HUB_ID}", headers=asha)
    assert ready.json()["offers"] == []


async def _pledge(db: AsyncSession, phone: str, meals: int) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await publish(
        db,
        billing_api.POINTS_DONATED,
        {
            "entry_id": str(entry_id),
            "user_id": str(await _user(db, phone)),
            "meals": meals,
            "points": meals * 500,
            "code": "child_meal",
        },
    )
    await deliver(db)
    return entry_id


async def test_meals_serve_the_oldest_pledges_first_and_never_more_than_pledged(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    store, s3 = _storage()
    db_session.add(_ngo())
    priya_entry = await _pledge(db_session, PRIYA, 2)
    await _pledge(db_session, SUNITA, 3)
    event = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.payload["entry_id"].astext == str(priya_entry))
        )
    ).scalar_one()
    await handoffs.on_points_donated(db_session, event)  # redelivered: counted once
    await handoffs.on_points_donated(
        db_session, OutboxEvent(event_type=event.event_type, payload={"meals": "two"})
    )
    pledges = list(
        (await db_session.execute(select(MealPledge).order_by(MealPledge.pledged_at))).scalars()
    )
    assert [(p.meals, p.served, p.hub_id) for p in pledges] == [
        (2, 0, SEED_HUB_ID),
        (3, 0, SEED_HUB_ID),
    ]
    vikram_id = await _user(db_session, VIKRAM)
    photos = [await _photo(db_session, s3, vikram_id) for _ in range(3)]
    await db_session.commit()
    api = clients(storage=store)
    vikram = await _as(api, google, VIKRAM)
    ready = await api.get(f"/v1/ngo-handoffs/ready?hub_id={SEED_HUB_ID}", headers=vikram)
    assert ready.json()["meals_waiting"] == 5

    def meals(n: int, photo: str, key: str) -> Any:
        body = {
            "ngo_id": str(SEED_NGO_ID),
            "hub_id": str(SEED_HUB_ID),
            "kind": "meals",
            "meals": n,
            "photo_keys": [photo],
        }
        return api.post("/v1/ngo-handoffs", json=body, headers={**vikram, **_key(key)})

    served = await meals(3, photos[0], "m1")
    assert served.status_code == 201, served.text
    assert (served.json()["meals"], served.json()["summary"]["meals"]) == (3, 3)
    for pledge in pledges:
        await db_session.refresh(pledge)
    assert [p.served for p in pledges] == [2, 1]  # the oldest whole, the next one split
    split = (
        await db_session.execute(
            select(HandoffMeal.pledge_id, HandoffMeal.meals).order_by(HandoffMeal.meals.desc())
        )
    ).all()
    assert [tuple(row) for row in split] == [(pledges[0].id, 2), (pledges[1].id, 1)]
    too_many = await meals(3, photos[1], "m2")
    assert (too_many.status_code, too_many.json()["code"], too_many.json()["meals_waiting"]) == (
        422,
        "community.meals_exceed_pledged",
        2,
    )
    ngo = await db_session.get(Ngo, SEED_NGO_ID)
    assert ngo is not None
    ngo.accepts = ["items", "food"]
    await db_session.commit()
    assert (await meals(1, photos[2], "m3")).json()["code"] == "community.ngo_does_not_accept"

    facts = await community_api.impact_facts(
        db_session, date_from=date.today() - timedelta(days=1), date_to=date.today() + timedelta(1)
    )
    assert sorted(
        (f.kind, f.meals, f.society_id) for f in facts if f.source in ("handoff", "pledge")
    ) == [
        ("handoff_meals", 3, None),
        ("meals_pledged", 2, None),
        ("meals_pledged", 3, None),
    ]
    exported = await community_api.export_for_user(db_session, await _user(db_session, PRIYA))
    assert [(p["meals"], p["served"]) for p in exported["meal_pledges"]] == [(2, 2)]


async def test_the_list_reports_by_role_and_never_shows_a_donor(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    store, s3 = _storage()
    roti_id = uuid.uuid4()
    db_session.add_all([_ngo(), _ngo(roti_id, name="Roti Bank")])
    gifts = [await _verified(db_session, PRIYA), await _verified(db_session, SUNITA)]
    vikram_id = await _user(db_session, VIKRAM)
    at = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
    for n, (gift, ngo_id) in enumerate(zip(gifts, (SEED_NGO_ID, roti_id), strict=True)):
        handoff = NgoHandoff(
            ngo_id=ngo_id,
            hub_id=SEED_HUB_ID,
            kind="items",
            items_count=5,
            kg_tenths=25,
            photo_keys=[await _photo(db_session, s3, vikram_id)],
            people_reached=10 * (n + 1),
            handed_at=at + timedelta(days=n),
            recorded_by="hub",
            recorded_by_user_id=vikram_id,
        )
        db_session.add(handoff)
        await db_session.flush()
        gift.status, gift.handoff_id = "handed_over", handoff.id
    ready_id = (await _verified(db_session, PRIYA)).id
    await db_session.commit()
    api = clients(storage=store)
    asha, vikram = await _as(api, google, ASHA), await _as(api, google, VIKRAM)
    neha, priya = await _as(api, google, NEHA), await _as(api, google, PRIYA)
    stranger = await _with_role(api, google, db_session, "+919800000955", "hub_manager", OTHER_HUB)

    listed = await api.get("/v1/ngo-handoffs?month=2026-10", headers=asha)
    assert listed.status_code == 200, listed.text
    assert listed.headers["Cache-Control"] == "no-store"
    assert [h["ngo"]["name"] for h in listed.json()["items"]] == ["Annapurna Food Bank"]
    assert listed.json()["report"] == {
        "handoffs": 1,
        "items": 5,
        "kg": 2.5,
        "meals": 0,
        "people_reached": 10,
    }
    assert len(listed.json()["items"][0]["photo_urls"]) == 1
    _private(listed)
    minted = await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "community.handoff_photo_links")
    )
    assert len(minted.scalars().all()) == 1
    assert (await api.get(f"/v1/ngo-handoffs?ngo_id={roti_id}", headers=asha)).json()[
        "code"
    ] == "community.ngo_not_found"

    hub = await api.get("/v1/ngo-handoffs?limit=1", headers=vikram)
    assert [h["ngo"]["name"] for h in hub.json()["items"]] == ["Roti Bank"]  # newest first
    assert hub.json()["report"]["handoffs"] == 2 and hub.json()["report"]["people_reached"] == 30
    rest = await api.get(
        f"/v1/ngo-handoffs?limit=1&cursor={hub.json()['next_cursor']}", headers=vikram
    )
    assert [h["ngo"]["name"] for h in rest.json()["items"]] == ["Annapurna Food Bank"]
    assert rest.json()["next_cursor"] is None
    assert (await api.get("/v1/ngo-handoffs?month=2026-09", headers=neha)).json()["items"] == []
    assert (await api.get("/v1/ngo-handoffs?month=2026-13", headers=neha)).status_code == 422
    assert (await api.get("/v1/ngo-handoffs", headers=stranger)).json()["items"] == []
    assert (
        await api.get(f"/v1/ngo-handoffs?hub_id={SEED_HUB_ID}", headers=stranger)
    ).status_code == 403
    assert (await api.get("/v1/ngo-handoffs", headers=priya)).status_code == 403

    ready = await api.get(f"/v1/ngo-handoffs/ready?hub_id={SEED_HUB_ID}", headers=asha)
    assert ready.status_code == 200, ready.text
    [donation] = ready.json()["donations"]
    assert donation == {
        "id": str(ready_id),
        "items": [
            {"code": "clothes", "unit": "kg", "qty": 2.5},
            {"code": "toys", "unit": "each", "qty": 2},
        ],
        "verified_on": drives.today().isoformat(),
    }
    _private(ready)
    assert (
        await api.get(f"/v1/ngo-handoffs/ready?hub_id={OTHER_HUB}", headers=asha)
    ).status_code == 403
    assert (
        await api.get(f"/v1/ngo-handoffs/ready?hub_id={SEED_HUB_ID}", headers=stranger)
    ).status_code == 403

    facts = await community_api.impact_facts(
        db_session, date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)
    )
    handed = [(f.kind, f.ngo_id, f.items, f.people_reached) for f in facts if f.source == "handoff"]
    assert handed == [("handoff_items", SEED_NGO_ID, 5, 10), ("handoff_items", roti_id, 5, 20)]


async def test_a_drive_names_an_ngo_that_takes_items(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    kitchen_id = uuid.uuid4()
    db_session.add_all([_ngo(), _ngo(kitchen_id, name="Roti Bank", accepts=["food"])])
    await db_session.commit()
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    today = drives.today()
    body = {
        "code": "school-bags",
        "name": {"en": "Bags", "hi": "Bags", "mr": "Bags"},
        "scope": "hub",
        "hub_id": str(SEED_HUB_ID),
        "codes": ["school_bag"],
        "multiplier": 2.0,
        "starts_on": today.isoformat(),
        "ends_on": (today + timedelta(days=9)).isoformat(),
    }

    def create(ngo_id: uuid.UUID, key: str) -> Any:
        return api.post(
            "/v1/admin/drives",
            json={**body, "ngo_id": str(ngo_id)},
            headers={**vikram, **_key(key)},
        )

    assert (await create(uuid.uuid4(), "u")).json()["code"] == "community.ngo_not_found"
    assert (await create(kitchen_id, "k")).json()["code"] == "community.ngo_does_not_accept"
    created = await create(SEED_NGO_ID, "d")
    assert created.status_code == 201, created.text
    assert created.json()["ngo"] == {"id": str(SEED_NGO_ID), "name": "Annapurna Food Bank"}
    cleared = await api.patch(
        f"/v1/admin/drives/{created.json()['id']}",
        json={"ngo_id": None},
        headers={**vikram, "If-Match": '"1"'},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["ngo"] is None
    priya = await _as(api, google, PRIYA)
    customer = (await api.get("/v1/drives", headers=priya)).json()["items"]
    assert [d["ngo"] for d in customer if d["code"] == "school-bags"] == [None]
