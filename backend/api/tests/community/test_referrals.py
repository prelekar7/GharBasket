"""Referrals (M9 §3.3): codes and links, the neighbour's claim and its refusals, the reward in
Kindness points on the first delivered box, the monthly cap, void and expiry, and privacy."""

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service as billing_service
from gharbasket.modules.billing.models import PointsLot
from gharbasket.modules.community import api as community_api
from gharbasket.modules.community import jobs, referrals
from gharbasket.modules.community.models import Referral, ReferralCode, SocietyRequest
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as orders_service
from gharbasket.modules.orders.models import Order
from gharbasket.platform.jobs import JobContext
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish
from gharbasket.tools.seed import SEED_HUB_ID

from ..conftest import make_settings
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .conftest import customer, deliver

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("captain_world")]

PRIYA = "+919800001001"  # Sunrise Towers: the referrer
SUNITA = "+919800001002"  # Green Valley Heights, flat B-504: the neighbour
RAMESH = "+919800001003"  # Krishna Kunj
CODE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{6}$")
CONVERTED_KEYS = {
    "referral_id",
    "referrer_user_id",
    "referee_user_id",
    "society_id",
    "hub_id",
    "referrer_points",
    "referee_points",
}


async def _as(api: Any, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def _user(db: AsyncSession, phone: str) -> uuid.UUID:
    user, _ = await find_or_create_user(db, phone)
    return user.id


async def _household(db: AsyncSession, phone: str) -> uuid.UUID:
    home = await identity_api.household_for_user(db, await _user(db, phone))
    assert home is not None
    return home.id


async def _code(api: Any, headers: dict[str, str]) -> str:
    response = await api.get("/v1/referrals", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["code"]


async def _claim(api: Any, headers: dict[str, str], code: str, key: str | None = None) -> Any:
    return await api.post(
        "/v1/referrals",
        json={"code": code},
        headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())},
    )


async def _points(db: AsyncSession, phone: str) -> int:
    account = await billing_service.find_account(
        db, "kindness", "user", await _user(db, phone), "KINDNESS_PTS"
    )
    return 0 if account is None else await billing_service.balance(db, account)


async def _delivered_box(db: AsyncSession, phone: str, kind: str = "box") -> uuid.UUID:
    """A delivered order of the household, its `orders.delivered` published and relayed."""
    household_id = await _household(db, phone)
    slot = await orders_service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"slot-{uuid.uuid4().hex[:8]}",
        kind="box",
        start=time(7, 0),
        end=time(9, 0),
        capacity=100,
    )
    home = await identity_api.household_for_user(db, await _user(db, phone))
    assert home is not None
    order = Order(
        household_id=household_id,
        hub_id=SEED_HUB_ID,
        society_id=home.society_id,
        kind=kind,
        delivery_date=date.today(),
        slot_id=slot.id,
        cutoff_at=datetime.now(UTC),
        status="delivered",
        subtotal_paise=50_000,
        total_paise=50_000,
    )
    db.add(order)
    await db.flush()
    payload = {"order_id": str(order.id), "household_id": str(household_id), "kind": kind}
    await publish(db, orders_api.DELIVERED, payload)
    await deliver(db)
    return order.id


async def _converted(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == community_api.REFERRAL_CONVERTED)
    )
    return [event.payload for event in rows.scalars()]


async def test_the_code_is_made_once_with_a_link_to_the_society(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya = await _as(api, google, PRIYA)

    first = await api.get("/v1/referrals", headers=priya)
    again = await api.get("/v1/referrals", headers={**priya, "Accept-Language": "hi"})

    body = first.json()
    code = body["code"]
    assert CODE.match(code) and again.json()["code"] == code
    assert first.headers["Cache-Control"] == "no-store"
    link = f"https://gharbasket.in/s/sunrise-towers-kharghar?ref={code}"
    assert body == {
        "code": code,
        "link": link,
        "share_text": body["share_text"],
        "rewards": {"referrer_points": 250, "referee_points": 250, "monthly_cap": 10},
        "summary": {"joined": 0, "converted": 0, "points_earned": 0},
        "referred_by": None,
        "items": [],
    }
    assert body["share_text"].endswith(link) and code in body["share_text"]
    assert "मेरा कोड" in again.json()["share_text"]
    # No household yet: a code, but no link.
    newcomer = (await api.get("/v1/referrals", headers=await customer(api, google, 7))).json()
    assert CODE.match(newcomer["code"]) and newcomer["link"] is None
    assert newcomer["code"] != code
    codes = (await db_session.execute(select(ReferralCode))).scalars().all()
    assert len(codes) == 2


async def test_a_neighbour_claims_a_code_once_and_never_learns_or_shows_names(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    code = await _code(api, priya)

    claimed = await _claim(api, sunita, code.lower(), key="claim-1")
    replay = await _claim(api, sunita, code.lower(), key="claim-1")

    assert claimed.status_code == 201, claimed.text
    assert replay.json() == claimed.json()
    assert claimed.json() == {
        "id": claimed.json()["id"],
        "status": "joined",
        "referee_points": 250,
        "converts_on": "first_delivered_box",
    }
    mine = (await api.get("/v1/referrals", headers=priya)).json()
    assert mine["summary"] == {"joined": 1, "converted": 0, "points_earned": 0}
    assert mine["items"] == [
        {
            "id": claimed.json()["id"],
            "status": "joined",
            "joined_on": referrals.today().isoformat(),
            "converted_on": None,
            "points": None,
        }
    ]
    sunita_user = await _user(db_session, SUNITA)
    text = str(mine)
    for secret in ("Sunita", "B-504", SUNITA, str(sunita_user)):
        assert secret not in text
    theirs = (await api.get("/v1/referrals", headers=sunita)).json()
    assert theirs["referred_by"] == {"status": "joined", "points": None}

    await db_session.commit()
    second = await _claim(api, sunita, await _code(api, await _as(api, google, RAMESH)))
    assert (second.status_code, second.json()["code"]) == (409, "community.already_referred")


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("own", 422, "community.referral_self"),
        ("unknown", 404, "community.referral_code_unknown"),
        ("no_household", 422, "community.household_required"),
        ("late", 409, "community.referral_window_closed"),
        ("delivered", 409, "community.referral_window_closed"),
    ],
)
async def test_claims_are_refused(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    case: str,
    status: int,
    code: str,
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    priya_code = await _code(api, priya)
    caller, used = sunita, priya_code
    if case == "own":
        caller = priya
    elif case == "unknown":
        used = "ZZZZZZ" if priya_code != "ZZZZZZ" else "YYYYYY"
    elif case == "no_household":
        caller = await customer(api, google, 8)
    elif case == "late":
        await db_session.execute(
            update(User)
            .where(User.id == await _user(db_session, SUNITA))
            .values(created_at=datetime.now(UTC) - timedelta(days=15))
        )
    else:
        await _delivered_box(db_session, SUNITA)
    await db_session.commit()

    refused = await _claim(api, caller, used)

    assert (refused.status_code, refused.json()["code"]) == (status, code)
    assert (await db_session.execute(select(Referral))).first() is None


async def test_the_first_delivered_box_rewards_both_sides_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    claimed = await _claim(api, sunita, await _code(api, priya))
    referral_id = uuid.UUID(claimed.json()["id"])

    # An add-on or a tiffin converts nothing.
    await _delivered_box(db_session, SUNITA, kind="addon")
    assert (await db_session.get(Referral, referral_id, populate_existing=True)).status == "joined"  # type: ignore[union-attr]
    order_id = await _delivered_box(db_session, SUNITA)
    (event,) = (
        await db_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == orders_api.DELIVERED,
                OutboxEvent.payload["order_id"].astext == str(order_id),
            )
        )
    ).scalars()
    await referrals.on_order_delivered(db_session, event)  # redelivered
    await _delivered_box(db_session, SUNITA)  # a second box

    referral = await db_session.get(Referral, referral_id, populate_existing=True)
    assert referral is not None
    assert (referral.status, referral.order_id, referral.converted_on) == (
        "converted",
        order_id,
        referrals.today(),
    )
    assert referral.referrer_entry_id is not None and referral.referee_entry_id is not None
    assert await _points(db_session, PRIYA) == 250
    assert await _points(db_session, SUNITA) == 250
    lots = (await db_session.execute(select(PointsLot.source_type))).scalars().all()
    assert sorted(lots) == ["referral", "referral_welcome"]
    (converted,) = await _converted(db_session)
    assert set(converted) == CONVERTED_KEYS
    assert (converted["referrer_points"], converted["referee_points"]) == (250, 250)
    mine = (await api.get("/v1/referrals", headers=priya)).json()
    assert mine["summary"] == {"joined": 1, "converted": 1, "points_earned": 250}
    assert mine["items"][0]["points"] == 250
    theirs = (await api.get("/v1/referrals", headers=sunita)).json()
    assert theirs["referred_by"] == {"status": "converted", "points": 250}


async def test_the_eleventh_conversion_in_a_month_pays_only_the_neighbour(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    code = await _code(api, priya)
    referrer = await _user(db_session, PRIYA)
    household = await _household(db_session, SUNITA)
    for _ in range(referrals.MONTHLY_CAP):  # ten rewarded this month already
        db_session.add(
            Referral(
                code=code,
                referrer_user_id=referrer,
                referee_user_id=uuid.uuid4(),
                referee_household_id=uuid.uuid4(),
                society_id=uuid.uuid4(),
                status="converted",
                joined_on=referrals.today(),
                converted_on=referrals.today(),
                referrer_points=250,
                referee_points=250,
            )
        )
    await db_session.flush()
    await _claim(api, sunita, code)

    await _delivered_box(db_session, SUNITA)

    referral = (
        await db_session.execute(
            select(Referral)
            .where(Referral.referee_household_id == household)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert (referral.status, referral.referrer_points, referral.referee_points) == (
        "converted",
        0,
        250,
    )
    assert referral.referrer_entry_id is None
    assert await _points(db_session, PRIYA) == 0
    assert await _points(db_session, SUNITA) == 250
    mine = (await api.get("/v1/referrals", headers=priya)).json()
    assert mine["summary"] == {"joined": 11, "converted": 11, "points_earned": 2500}
    assert mine["items"][0]["points"] == 0


async def test_open_referrals_expire_after_90_days_and_void_on_deletion(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    ramesh = await _as(api, google, RAMESH)
    code = await _code(api, priya)
    old = uuid.UUID((await _claim(api, sunita, code)).json()["id"])
    recent = uuid.UUID((await _claim(api, ramesh, code)).json()["id"])
    today = referrals.today()
    await db_session.execute(
        update(Referral)
        .where(Referral.id == old)
        .values(joined_on=today - timedelta(days=referrals.EXPIRY_DAYS))
    )
    await db_session.execute(
        update(Referral).where(Referral.id == recent).values(joined_on=today - timedelta(days=89))
    )
    ctx = JobContext(
        session=db_session,
        scheduled_for=datetime.now(UTC),
        settings=make_settings(),
    )

    expired = await jobs.expire_referrals(ctx)

    assert expired["voided"] == 1
    rows = {
        row.id: (row.status, row.void_reason)
        for row in (
            await db_session.execute(select(Referral).execution_options(populate_existing=True))
        ).scalars()
    }
    assert rows == {old: ("void", "expired"), recent: ("joined", None)}

    await publish(
        db_session,
        identity_api.DELETION_REQUESTED,
        {"user_id": str(await _user(db_session, PRIYA)), "household_id": None, "purge_on": None},
    )
    await deliver(db_session)
    # Void referrals pay nothing, even when a box arrives.
    await _delivered_box(db_session, RAMESH)
    voided = await db_session.get(Referral, recent, populate_existing=True)
    assert voided is not None and (voided.status, voided.void_reason) == ("void", "deletion")
    assert await _points(db_session, RAMESH) == 0
    for payload in ({}, {"user_id": "x"}):
        await referrals.on_deletion_requested(
            db_session, OutboxEvent(event_type=identity_api.DELETION_REQUESTED, payload=payload)
        )
    for payload in ({"kind": "box"}, {"kind": "box", "household_id": "x", "order_id": "y"}):
        await referrals.on_order_delivered(
            db_session, OutboxEvent(event_type=orders_api.DELIVERED, payload=payload)
        )


async def test_export_never_names_the_neighbour_and_erasure_keeps_the_numbers(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    code = await _code(api, priya)
    await _claim(api, sunita, code)
    await _delivered_box(db_session, SUNITA)
    referrer, neighbour = await _user(db_session, PRIYA), await _user(db_session, SUNITA)
    db_session.add(SocietyRequest(user_id=referrer, name="Panvel Heights"))
    await db_session.flush()

    exported = await community_api.export_for_user(db_session, referrer)
    neighbour_export = await community_api.export_for_user(db_session, neighbour)

    made = {"status": "converted", "joined_on": referrals.today(), "points": 250}
    assert exported["referral_code"] == code
    assert exported["referrals_made"] == [{**made, "converted_on": referrals.today()}]
    assert exported["referred"] is None
    assert neighbour_export["referred"] == {**made, "converted_on": referrals.today()}
    assert str(neighbour) not in str(exported)
    facts = await community_api.referral_facts(
        db_session, date_from=referrals.today(), date_to=referrals.today(), hub_ids=[SEED_HUB_ID]
    )
    assert [(f.status, f.referrer_points, f.data_source) for f in facts] == [
        ("converted", 250, "synthetic")
    ]
    assert "referrer_user_id" not in str(facts)

    erased = await community_api.anonymise_for_user(db_session, referrer)

    assert erased == {"referral_codes": 1, "society_requests": 1, "referrals": 0}
    assert await db_session.get(ReferralCode, referrer) is None
    assert (
        len(
            await community_api.referral_facts(
                db_session, date_from=referrals.today(), date_to=referrals.today()
            )
        )
        == 1
    )
