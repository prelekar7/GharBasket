"""The public cook page (M8 §3.5): only with her consent, with less on it than the card, the same
404 for every way it is not there, cached and rate-limited like M12's society page."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.modules.catalog.models import Product
from gharbasket.modules.identity.models import User
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import kitchens, service
from gharbasket.modules.partners.models import (
    Assignment,
    AssignmentOrder,
    Partner,
    Rating,
    TrainingAttempt,
    TrainingModule,
)
from gharbasket.platform import rate_limit
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.seed import SEED_HUB_ID

from .. import seed_cache as cached
from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.conftest import valkey as valkey
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, PRIYA, VIKRAM, as_user
from .tiffins import kitchen, kitchen_row, tag, tiffin_order, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("cook_world")]

ME = "/v1/kitchens/me"
HANDLE = re.compile(r"[a-z2-7]{10}")
NOW = datetime(2027, 3, 1, 6, 30, tzinfo=UTC)  # 1 March 2027, 12:00 IST
KEYS = {
    "handle",
    "display_name",
    "photo_url",
    "hub",
    "since",
    "fssai",
    "hygiene",
    "trainings",
    "rating",
    "skills",
    "dishes",
}


@pytest.fixture
async def cook_world(db_session: AsyncSession) -> None:
    await cached.seed(db_session)
    await cached.seed_societies(db_session)
    await cached.seed_households(db_session)
    await cached.seed_catalog(db_session)
    await cached.seed_hubs(db_session)
    await cached.seed_partners(db_session)
    await cached.seed_trainings(db_session)
    await tag(db_session, "tomato", "veg")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kitchens, "now", lambda: NOW)


async def switch(api: httpx2.AsyncClient, auth: dict[str, str], on: bool) -> httpx2.Response:
    etag = (await api.get(ME, headers=auth)).headers["ETag"]
    return await api.patch(ME, json={"public_profile": on}, headers={**auth, "If-Match": etag})


async def turned_on(
    api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession
) -> tuple[uuid.UUID, str, dict[str, str]]:
    """Lata's kitchen with her public page on: (partner id, handle, her auth header)."""
    partner_id = await kitchen(db, LATA, "Lata tai")
    lata, _ = await as_user(api, google, LATA)
    answer = await switch(api, lata, True)
    assert answer.status_code == 200, answer.text
    return partner_id, answer.json()["public_profile"]["handle"], lata


def stable(answer: httpx2.Response) -> dict[str, Any]:
    """A problem body without what names the request (its path and id)."""
    return {k: v for k, v in answer.json().items() if k not in ("instance", "request_id")}


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


async def test_only_she_turns_it_on_and_off_and_on_again_mints_a_new_handle(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients(public_web_url="https://example.in/")
    partner_id = await kitchen(db_session, LATA, "Lata tai")
    lata, lata_user = await as_user(api, google, LATA)
    vikram, _ = await as_user(api, google, VIKRAM)

    off = await api.get(ME, headers=lata)
    assert off.json()["public_profile"] is None  # off by default
    # Hub staff cannot turn it on: /kitchens/me is hers alone, and the inspection takes no switch.
    staff = await api.patch(
        ME, json={"public_profile": True}, headers={**vikram, "If-Match": '"1"'}
    )
    inspection = await api.post(
        f"/v1/kitchens/{partner_id}/inspection",
        json={"grade": "A", "public_profile": True},
        headers={**vikram, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert staff.status_code in (403, 404) and inspection.status_code == 422
    assert (await kitchen_row(db_session, partner_id)).public_handle is None

    on = await switch(api, lata, True)
    assert on.status_code == 200, on.text
    profile = on.json()["public_profile"]
    first = profile["handle"]
    assert HANDLE.fullmatch(first) and first not in str(partner_id)
    assert profile["url"] == f"https://example.in/cook/{first}" and profile["since"]
    assert on.headers["ETag"] == f'"{on.json()["version"]}"'
    again = await switch(api, lata, True)  # already on: the same handle, no new audit row
    assert again.json()["public_profile"]["handle"] == first
    assert (await api.get(f"/v1/public/cooks/{first}")).status_code == 200

    gone = await switch(api, lata, False)
    assert gone.json()["public_profile"] is None
    row = await kitchen_row(db_session, partner_id)
    assert (row.public_handle, row.public_since) == (None, None)
    assert (await api.get(f"/v1/public/cooks/{first}")).status_code == 404

    back = await switch(api, lata, True)
    second = back.json()["public_profile"]["handle"]
    assert second != first and HANDLE.fullmatch(second)
    assert (await api.get(f"/v1/public/cooks/{first}")).status_code == 404
    assert (await api.get(f"/v1/public/cooks/{second}")).status_code == 200

    audits = await db_session.execute(
        select(AuditEntry)
        .where(AuditEntry.action == "kitchen.public_profile")
        .order_by(AuditEntry.id)
    )
    rows = audits.scalars().all()
    assert [r.after for r in rows] == [{"on": True}, {"on": False}, {"on": True}]
    assert {(r.actor_user_id, r.entity_type, r.entity_id) for r in rows} == {
        (lata_user, "kitchen", str(partner_id))
    }


async def test_a_handle_that_is_taken_is_drawn_again(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    lata_id = await kitchen(db_session, LATA, "Lata tai")
    priya_id = await kitchen(db_session, PRIYA, "Priya")
    (await kitchen_row(db_session, priya_id)).public_handle = "aaaaaaaaaa"
    await db_session.flush()
    draws = iter(["aaaaaaaaaa", "bbbbbbbbbb"])
    monkeypatch.setattr(kitchens, "new_handle", lambda: next(draws))
    partner = await db_session.get(Partner, lata_id)
    row = await kitchen_row(db_session, lata_id)
    assert partner is not None

    await kitchens.set_public(db_session, partner, row, on=True)

    assert row.public_handle == "bbbbbbbbbb" and row.public_since is not None

    # Every draw taken: a deliberate error, not a handle that is someone else's.
    monkeypatch.setattr(kitchens, "new_handle", lambda: "aaaaaaaaaa")
    lata = await kitchen_row(db_session, lata_id)
    await kitchens.set_public(db_session, partner, lata, on=False)
    with pytest.raises(kitchens.HandleUnavailable):
        await kitchens.set_public(db_session, partner, lata, on=True)
    assert lata.public_handle is None


async def test_the_page_needs_no_sign_in_and_has_exactly_the_contracts_keys(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    partner_id, handle, _ = await turned_on(api, google, db_session)
    row = await kitchen_row(db_session, partner_id)
    row.area, row.address = "Sector 20, Kharghar", "B-1103, Sunrise Towers"
    row.fssai_reg_no, row.fssai_valid_until = "11521999000123", datetime(2028, 3, 31).date()
    row.hygiene_grade, row.inspected_on = "A", datetime(2026, 12, 20).date()
    partner = await db_session.get(Partner, partner_id)
    assert partner is not None
    partner.rating_avg, partner.rating_count = Decimal("4.60"), 23
    db_session.add(
        Rating(
            order_id=uuid.uuid4(),
            partner_id=partner_id,
            household_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            score=5,
            comment="secret words",
        )
    )
    await db_session.commit()

    lata, _ = await as_user(api, google, LATA)
    await db_session.refresh(row)
    assert row.lat is not None and row.lng is not None  # the kitchen has a pin (`kitchen()`)
    user = await db_session.get(User, await user_id(db_session, LATA))
    assert user is not None and user.phone_e164  # and she has a phone
    phone = user.phone_e164

    answer = await api.get(f"/v1/public/cooks/{handle}")  # no Authorization header at all
    signed_in = await api.get(f"/v1/public/cooks/{handle}", headers=lata)

    assert answer.status_code == 200 == signed_in.status_code, answer.text
    body = answer.json()
    assert signed_in.json() == body  # signing in shows nothing more
    for header in ("Cache-Control", "Vary", "Content-Language"):
        assert signed_in.headers[header] == answer.headers[header], header
    assert set(body) == KEYS
    assert set(body["hub"]) == {"name"} and set(body["fssai"]) == {"reg_no", "valid_until"}
    assert set(body["hygiene"]) == {"grade", "inspected_on"}
    assert set(body["rating"]) == {"average", "count"}
    assert body["handle"] == handle and body["display_name"] == "Lata tai"
    assert body["hub"] == {"name": "Kharghar hub"} and body["photo_url"] is None
    assert body["fssai"] == {"reg_no": "11521999000123", "valid_until": "2028-03-31"}
    assert body["hygiene"] == {"grade": "A", "inspected_on": "2026-12-20"}
    assert body["rating"] == {"average": 4.6, "count": 23} and body["skills"] == ["veg"]
    assert (body["trainings"], body["dishes"]) == ([], [])
    assert re.fullmatch(r"\d{4}-\d{2}", body["since"])
    forbidden = {"area", "address", "location", "phone", "portions_served", "partner_id"}
    assert not forbidden & keys_of(body)
    text = json.dumps(body, ensure_ascii=False)
    traces = ("Sector 20", "B-1103", str(partner_id), "secret words", "comment", "+91")
    for trace in (*traces, phone, phone[3:], f"{row.lat:.4f}", f"{row.lng:.4f}"):
        assert trace not in text, trace
    # The route is public in the contract too: no security requirement in the OpenAPI document.
    operation = create_app(make_settings()).openapi()["paths"]["/v1/public/cooks/{handle}"]["get"]
    assert not operation.get("security")


async def test_every_missing_page_is_the_same_404_and_a_paused_kitchen_still_answers(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    partner_id, handle, lata = await turned_on(api, google, db_session)
    vikram = await user_id(db_session, VIKRAM)
    partner = await db_session.get(Partner, partner_id)
    assert partner is not None
    # Priya's kitchen had a page, then she left.
    priya_id = await kitchen(db_session, PRIYA, "Priya")
    priya = await db_session.get(Partner, priya_id)
    assert priya is not None
    await kitchens.set_public(db_session, priya, await kitchen_row(db_session, priya_id), on=True)
    departed = (await kitchen_row(db_session, priya_id)).public_handle
    await service.leave_for_user(db_session, priya.user_id)
    await db_session.commit()

    unknown = await api.get("/v1/public/cooks/zzzzzzzzzz")
    malformed = await api.get("/v1/public/cooks/NOT-A-HANDLE")
    upper = await api.get(f"/v1/public/cooks/{handle.upper()}")  # handles are lower case
    by_id = await api.get(f"/v1/public/cooks/{partner_id}")  # never the partner id
    left = await api.get(f"/v1/public/cooks/{departed}")
    partner.status = "paused"
    await db_session.commit()
    paused = await api.get(f"/v1/public/cooks/{handle}")
    partner.status = "active"
    await db_session.commit()
    await switch(api, lata, False)
    off = await api.get(f"/v1/public/cooks/{handle}")
    second = (await switch(api, lata, True)).json()["public_profile"]["handle"]

    await db_session.refresh(partner)  # the refused requests rolled the shared session back
    await service.suspend(db_session, partner, reason_code="hygiene", note=None, by_user_id=vikram)
    await db_session.commit()
    suspended = await api.get(f"/v1/public/cooks/{second}")
    cleared = await kitchen_row(db_session, partner_id)
    assert (cleared.public_handle, cleared.public_since) == (None, None)
    await db_session.refresh(partner)
    await service.reinstate(db_session, partner, note=None, by_user_id=vikram)
    await db_session.commit()
    reinstated = await api.get(f"/v1/public/cooks/{second}")  # she turns it on again herself

    assert paused.status_code == 200
    assert (await kitchen_row(db_session, partner_id)).public_handle is None
    missing = [unknown, malformed, upper, by_id, left, off, suspended, reinstated]
    assert {a.status_code for a in missing} == {404}
    assert all(stable(a) == stable(unknown) for a in missing)
    assert {a.headers["Cache-Control"] for a in missing} == {"no-store"}
    assert unknown.json()["code"] == "partners.cook_not_found"
    clears = await db_session.execute(
        select(AuditEntry.actor_user_id, AuditEntry.entity_id, AuditEntry.after).where(
            AuditEntry.action == "kitchen.public_profile",
            AuditEntry.after["reason"].is_not(None),
        )
    )
    assert sorted(clears.all(), key=str) == sorted(
        [
            (None, str(priya_id), {"on": False, "reason": "left"}),
            (vikram, str(partner_id), {"on": False, "reason": "suspended"}),
        ],
        key=str,
    )


async def test_leaving_and_erasure_clear_the_handle_and_the_export_shows_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    partner_id, handle, _ = await turned_on(api, google, db_session)
    lata_user = await user_id(db_session, LATA)
    exported = await partners_api.export_for_user(db_session, lata_user)
    row = await kitchen_row(db_session, partner_id)
    assert exported["public_profile"] == {"since": row.public_since}

    await service.leave_for_user(db_session, lata_user)
    await db_session.commit()

    await db_session.refresh(row)
    assert (row.public_handle, row.public_since) == (None, None)
    assert (await api.get(f"/v1/public/cooks/{handle}")).status_code == 404
    assert (await partners_api.export_for_user(db_session, lata_user))["public_profile"] is None

    # Erasure clears it too, even for a record that left with its page somehow still on.
    row.public_handle, row.public_since = handle, datetime.now(UTC)
    await db_session.flush()
    counts = await partners_api.anonymise_for_user(db_session, lata_user, None)
    await db_session.refresh(row)
    assert counts["kitchens"] == 1
    assert (row.public_handle, row.public_since) == (None, None)
    reasons = await db_session.execute(
        select(AuditEntry.actor_user_id, AuditEntry.after)
        .where(
            AuditEntry.action == "kitchen.public_profile", AuditEntry.entity_id == str(partner_id)
        )
        .order_by(AuditEntry.id)
    )
    assert reasons.all()[-2:] == [
        (None, {"on": False, "reason": "left"}),
        (None, {"on": False, "reason": "erased"}),  # no actor: the purge keeps no user id
    ]


async def test_trainings_and_dishes_follow_accept_language(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: None
) -> None:
    api = clients()
    partner_id, handle, _ = await turned_on(api, google, db_session)
    lata_user = await user_id(db_session, LATA)
    for module, passed in (
        ("kitchen_ops", True),
        ("packing", True),  # a packer's module: never on her page
        ("captain_handover", True),  # a captain's: never on her page
        ("hygiene", False),
        ("hygiene", True),  # a later pass counts
        ("app_basics", True),
        ("rider_safety", False),  # never passed: not shown
    ):
        db_session.add(
            TrainingAttempt(
                user_id=lata_user,
                module_code=module,
                module_version=1,
                score=Decimal("1.000") if passed else Decimal("0.200"),
                passed=passed,
                answers=[],
            )
        )
    ops = await db_session.get(TrainingModule, ("kitchen_ops", 1))
    assert ops is not None
    ops.texts = {k: v for k, v in ops.texts.items() if k != "mr"}  # no Marathi: English
    # Dishes: portions per product over the last 60 IST days (1 Jan to 1 Mar 2027).
    today = NOW.date()
    plan = [
        ("palak", 4, 0, "delivered"),
        ("palak", 5, 59, "delivered"),  # the 60th day back is still in: 9 portions
        ("methi", 8, 1, "delivered"),
        ("coriander", 7, 2, "delivered"),
        ("mint", 6, 3, "delivered"),
        ("dill", 5, 4, "delivered"),
        ("curry-leaves", 4, 5, "delivered"),
        ("red-amaranth", 3, 6, "delivered"),  # seventh: cut
        ("tomato", 20, 60, "delivered"),  # 61 days back: out of the window
        ("bhindi", 30, 1, "failed"),  # not delivered
        ("brinjal", 40, 2, "delivered"),  # the most, but no longer sold: left out
    ]
    for slug, portions, days_back, status in plan:
        order = await tiffin_order(db_session, PRIYA, 1, slug=slug)
        assignment = Assignment(
            partner_id=partner_id,
            hub_id=SEED_HUB_ID,
            on_date=today - timedelta(days=days_back),
            meal="lunch",
            slot_id=uuid.uuid4(),
            round=1,
            source="manual",
            status="completed",
            portions=portions,
            respond_by=NOW,
        )
        db_session.add(assignment)
        await db_session.flush()
        db_session.add(
            AssignmentOrder(
                assignment_id=assignment.id,
                order_id=order,
                portions=portions,
                short_code=uuid.uuid4().hex[:6].upper(),
                status=status,
            )
        )
    await db_session.execute(update(Product).where(Product.slug == "brinjal").values(active=False))
    await db_session.commit()

    english = await api.get(f"/v1/public/cooks/{handle}")
    hindi = await api.get(f"/v1/public/cooks/{handle}", headers={"Accept-Language": "hi-IN,hi"})
    marathi = await api.get(f"/v1/public/cooks/{handle}", headers={"Accept-Language": "mr"})

    for answer in (english, hindi, marathi):
        assert answer.status_code == 200, answer.text
        assert "Accept-Language" in answer.headers["Vary"]
    assert english.headers["Content-Language"] == "en"
    codes = [t["code"] for t in english.json()["trainings"]]
    assert codes == ["app_basics", "hygiene", "kitchen_ops"]
    assert "packing" not in codes and "captain_handover" not in codes
    assert {frozenset(t) for t in english.json()["trainings"]} == {frozenset({"code", "title"})}
    assert {frozenset(d) for d in english.json()["dishes"]} == {frozenset({"name"})}
    assert [t["title"] for t in english.json()["trainings"]][:2] == [
        "Using the GharBasket app",
        "Kitchen hygiene",
    ]
    assert hindi.json()["trainings"][1]["title"] == "रसोई की स्वच्छता"
    assert marathi.json()["trainings"][2]["title"] == "Running your tiffin kitchen"
    assert english.json()["dishes"] == [
        {"name": name}
        for name in (
            "Spinach (palak)",
            "Fenugreek leaves (methi)",
            "Coriander (dhania)",
            "Mint (pudina)",
            "Dill (shepu)",
            "Curry leaves (kadi patta)",
        )
    ]
    assert [d["name"] for d in hindi.json()["dishes"]][:3] == ["पालक", "मेथी", "हरा धनिया"]


async def test_cache_headers_on_200_no_store_on_errors_and_a_shared_per_ip_limit(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    valkey: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, handle, _ = await turned_on(clients(), google, db_session)
    monkeypatch.setattr(rate_limit, "PUBLIC_PER_MINUTE", 3)
    api = clients(valkey=valkey)

    found = await api.get(f"/v1/public/cooks/{handle}")
    missing = await api.get("/v1/public/cooks/zzzzzzzzzz")
    society = await api.get("/v1/public/societies/sunrise-towers-kharghar")
    limited = await api.get(f"/v1/public/cooks/{handle}")

    assert found.status_code == 200
    assert found.headers["Cache-Control"] == "public, max-age=300, stale-while-revalidate=600"
    assert missing.status_code == 404
    assert missing.headers["Cache-Control"] == "no-store"
    assert society.status_code != 429  # the society page counts against the same IP
    assert limited.status_code == 429
    assert limited.json()["code"] == "request.rate_limited" and "Retry-After" in limited.headers
    assert limited.headers["Cache-Control"] == "no-store"
    society_missing = await api.get("/v1/public/societies/no-such-society")
    assert society_missing.status_code == 429  # still the same counter
    assert society_missing.headers["Cache-Control"] == "no-store"
