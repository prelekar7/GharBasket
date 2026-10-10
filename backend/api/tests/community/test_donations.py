"""Kindness donations (M9 §3.4): the pickup dates and their 22:00 close, scheduling and its
refusals, drive multipliers and the points formula, photos, the open-donation limit, cancelling,
drive progress under k = 5, and the data export."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import time_machine
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.community import donations
from gharbasket.modules.community.kindness_schemas import DonationIn
from gharbasket.modules.community.models import Donation, DonationDrive
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.subscriptions.models import SocietyDeliveryDay
from gharbasket.platform.files import create_store
from gharbasket.platform.models import OutboxEvent, Upload
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, SOCIETIES

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from ..test_files import SETTINGS as STORAGE_SETTINGS
from ..test_files import FakeS3

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("donation_world")]

PRIYA = "+919800001001"  # Sunrise Towers
SUNITA = "+919800001002"  # Green Valley Heights
RAMESH = "+919800001003"  # Krishna Kunj
MEENA = "+919800001004"  # Sunrise Towers
SAVITA = "+919800001012"  # Krishna Kunj
VIKRAM = "+919800001008"  # hub manager
GREEN_VALLEY = next(s.id for s in SOCIETIES if s.slug == "green-valley-heights-kharghar")
# Monday 12 October 2026, 20:30 IST: Tuesday the 13th is still open until 22:00.
MONDAY_EVENING = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
TUE, NEXT_TUE, TUE_AFTER = date(2026, 10, 13), date(2026, 10, 20), date(2026, 10, 27)


@pytest.fixture(autouse=True)
def monday() -> Iterator[None]:
    with time_machine.travel(MONDAY_EVENING, tick=True):
        yield


async def _as(api: Any, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def _user(db: AsyncSession, phone: str) -> uuid.UUID:
    user, _ = await find_or_create_user(db, phone)
    return user.id


def _drive(code: str, **fields: Any) -> DonationDrive:
    values: dict[str, Any] = {
        "scope": "hub",
        "hub_id": SEED_HUB_ID,
        "codes": ["clothes", "utensils"],
        "multiplier": Decimal("2.0"),
        "starts_on": date(2026, 10, 1),
        "ends_on": date(2026, 10, 31),
        **fields,
    }
    return DonationDrive(
        code=code, name={"en": code, "hi": code, "mr": code}, created_by=uuid.uuid4(), **values
    )


def _body(**fields: Any) -> dict[str, Any]:
    return {"items": [{"code": "toys", "qty": 2}], "pickup_on": TUE.isoformat(), **fields}


async def _payloads(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


async def test_points_round_half_up_and_the_largest_line_takes_the_remainder() -> None:
    split = donations.split_points
    assert split([Decimal("22.5")]) == [23]  # half up, not to even (22)
    assert split([Decimal("22.5"), Decimal("22.5")]) == [22, 23]
    assert sum(split([Decimal("22.5"), Decimal("22.5")])) == 45
    assert split([Decimal("100"), Decimal("0.4"), Decimal("0.4")]) == [101, 0, 0]
    assert split([]) == []
    assert donations.qty_ok(Decimal("2.5"), "kg", Decimal(15))
    assert not donations.qty_ok(Decimal("2.25"), "kg", Decimal(15))
    assert not donations.qty_ok(Decimal("1.5"), "each", Decimal(10))
    assert not donations.qty_ok(Decimal(0), "each", Decimal(10))
    assert donations.qty_ok(Decimal(0), "each", Decimal(10), zero=True)


async def test_pickup_dates_are_the_next_two_open_ones_and_close_at_22(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    answer = await api.get("/v1/donations", headers=priya)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "items": [],
        "next_cursor": None,
        "pickup_dates": [TUE.isoformat(), NEXT_TUE.isoformat()],
    }
    with time_machine.travel(datetime(2026, 10, 12, 16, 30, tzinfo=UTC), tick=False):  # 22:00
        priya = await _as(api, google, PRIYA)  # a fresh token at the later time
        late = await api.get("/v1/donations", headers=priya)
        assert late.json()["pickup_dates"] == [NEXT_TUE.isoformat(), TUE_AFTER.isoformat()]
        closed = await api.post(
            "/v1/donations", json=_body(), headers={**priya, "Idempotency-Key": "late"}
        )
        assert closed.json()["code"] == "community.pickup_date_invalid"
        assert closed.json()["dates"] == [NEXT_TUE.isoformat(), TUE_AFTER.isoformat()]

    await db_session.execute(
        delete(SocietyDeliveryDay).where(SocietyDeliveryDay.society_id == GREEN_VALLEY)
    )
    await db_session.commit()
    sunita = await _as(api, google, SUNITA)
    assert (await api.get("/v1/donations", headers=sunita)).json()["pickup_dates"] == []
    none = await api.post("/v1/donations", json=_body(), headers={**sunita, "Idempotency-Key": "n"})
    assert (none.status_code, none.json()["code"]) == (422, "community.no_pickup_day")


async def test_scheduling_applies_the_drive_to_its_codes_and_publishes_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    diwali = _drive("diwali")
    neighbours = _drive("neighbours", scope="society", society_id=GREEN_VALLEY)
    over = _drive("over", ends_on=date(2026, 10, 12))
    db_session.add_all([diwali, neighbours, over])
    await db_session.commit()
    ids = {d.code: str(d.id) for d in (diwali, neighbours, over)}
    api = clients()
    priya = await _as(api, google, PRIYA)

    def post(body: dict[str, Any], key: str) -> Any:
        return api.post("/v1/donations", json=body, headers={**priya, "Idempotency-Key": key})

    body = _body(
        items=[{"code": "clothes", "qty": 2.5}, {"code": "toys", "qty": 3}],
        drive_id=ids["diwali"],
    )
    created = await post(body, "d1")
    assert created.status_code == 201, created.text
    out = created.json()
    # clothes 2.5 kg x 40 x 2.0 (in the drive) + toys 3 x 15 (not in it)
    assert out["estimated_points"] == 245
    assert (out["status"], out["photos"], out["points_awarded"], out["ngo"]) == (
        "scheduled",
        0,
        None,
        None,
    )
    assert out["drive"] == {"id": ids["diwali"], "name": "diwali", "multiplier": 2.0}
    assert out["items"][0] == {
        "code": "clothes",
        "unit": "kg",
        "qty": 2.5,
        "counted": None,
        "points": None,
    }
    assert [t["status"] for t in out["timeline"]] == ["scheduled"]
    assert (await post(body, "d1")).json()["id"] == out["id"]  # replayed
    [payload] = await _payloads(db_session, "community.donation_scheduled")
    assert payload["est_kg_tenths"] == 25 and payload["pickup_on"] == TUE.isoformat()
    assert set(payload) == {
        "donation_id",
        "user_id",
        "household_id",
        "hub_id",
        "society_id",
        "pickup_on",
        "items",
        "est_kg_tenths",
    }

    refusals = [
        ({"items": []}, "community.no_items"),
        ({"items": [{"code": "sofas", "qty": 1}]}, "community.item_code_unknown"),
        ({"items": [{"code": "clothes", "qty": 2.25}]}, "community.donation_qty_invalid"),
        ({"items": [{"code": "clothes", "qty": 16}]}, "community.donation_qty_invalid"),
        ({"items": [{"code": "toys", "qty": 1.5}]}, "community.donation_qty_invalid"),
        ({"items": [{"code": "toys", "qty": 0}]}, "community.donation_qty_invalid"),
        ({"items": [{"code": "toys", "qty": 1}] * 2}, "request.invalid"),
        ({"pickup_on": "2026-10-14"}, "community.pickup_date_invalid"),
        ({"drive_id": ids["neighbours"]}, "community.drive_not_open"),
        ({"drive_id": ids["over"]}, "community.drive_not_open"),
        ({"drive_id": str(uuid.uuid4())}, "community.drive_not_open"),
        ({"photo_keys": ["donation_photo/x.jpg"]}, "platform.storage_unavailable"),
    ]
    for n, (fields, code) in enumerate(refusals):
        refused = await post(_body(**fields), f"r{n}")
        assert refused.json()["code"] == code, (fields, refused.text)


async def test_photos_must_be_the_callers_own_donation_uploads(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    store = create_store(STORAGE_SETTINGS)
    assert store is not None
    store.client = s3
    priya_id, sunita_id = await _user(db_session, PRIYA), await _user(db_session, SUNITA)

    async def upload(user_id: uuid.UUID, purpose: str = "donation_photo") -> str:
        key = f"{purpose}/{user_id}/{uuid.uuid4()}.jpg"
        db_session.add(
            Upload(
                key=key, user_id=user_id, purpose=purpose, content_type="image/jpeg", size_bytes=9
            )
        )
        s3.objects[key] = ("image/jpeg", 9)
        return key

    mine, theirs = await upload(priya_id), await upload(sunita_id)
    kyc = await upload(priya_id, "kyc_document")
    await db_session.commit()
    api = clients(storage=store)
    priya = await _as(api, google, PRIYA)
    for n, bad in enumerate((theirs, kyc, "donation_photo/nothing.jpg")):
        refused = await api.post(
            "/v1/donations",
            json=_body(photo_keys=[bad]),
            headers={**priya, "Idempotency-Key": f"bad{n}"},
        )
        assert refused.json()["code"] == "community.photo_key_invalid"
    created = await api.post(
        "/v1/donations",
        json=_body(photo_keys=[mine, mine]),
        headers={**priya, "Idempotency-Key": "ok"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["photos"] == 1 and "photo_keys" not in created.json()
    claimed = await db_session.get(Upload, mine)
    assert (
        claimed is not None and claimed.claimed_by == "community" and claimed.delete_after is None
    )
    again = await api.post(
        "/v1/donations", json=_body(photo_keys=[mine]), headers={**priya, "Idempotency-Key": "re"}
    )
    assert again.json()["code"] == "community.photo_key_invalid"

    cancelled = await api.post(
        f"/v1/donations/{created.json()['id']}/cancel",
        headers={**priya, "Idempotency-Key": "c"},
    )
    assert cancelled.json()["cancel_reason"] == "customer"
    await db_session.refresh(claimed)
    assert claimed.delete_after is not None  # 90 days from the cancel


async def test_three_open_donations_at_most_and_cancelling_frees_a_place(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    await db_session.commit()

    def post(key: str) -> Any:
        return api.post("/v1/donations", json=_body(), headers={**priya, "Idempotency-Key": key})

    made = [(await post(f"d{n}")).json()["id"] for n in range(3)]
    fourth = await post("d3")
    assert (fourth.status_code, fourth.json()["code"]) == (409, "community.too_many_open_donations")

    url = f"/v1/donations/{made[0]}/cancel"
    stranger = await api.post(url, headers={**sunita, "Idempotency-Key": "s"})
    assert (stranger.status_code, stranger.json()["code"]) == (404, "community.donation_not_found")
    cancelled = await api.post(url, headers={**priya, "Idempotency-Key": "c1"})
    assert cancelled.status_code == 200, cancelled.text
    assert [t["status"] for t in cancelled.json()["timeline"]] == ["scheduled", "cancelled"]
    twice = await api.post(url, headers={**priya, "Idempotency-Key": "c2"})
    assert (twice.status_code, twice.json()["code"]) == (409, "community.donation_not_cancellable")
    [payload] = await _payloads(db_session, "community.donation_cancelled")
    assert payload == {
        "donation_id": made[0],
        "hub_id": str(SEED_HUB_ID),
        "society_id": str(SEED_SOCIETY_ID),
        "pickup_on": TUE.isoformat(),
        "reason": "customer",
    }
    assert (await post("d4")).status_code == 201

    listed = await api.get("/v1/donations?limit=2", headers=priya)
    page2 = await api.get(
        f"/v1/donations?limit=2&cursor={listed.json()['next_cursor']}", headers=priya
    )
    ids = [d["id"] for d in listed.json()["items"] + page2.json()["items"]]
    assert len(ids) == 4 and ids == sorted(ids, reverse=True)  # newest first
    export = await community_api.export_for_user(db_session, await _user(db_session, PRIYA))
    assert len(export["donations"]) == 4 and export["donations"][0]["photos"] == 0


async def test_drive_progress_needs_five_households_for_customers(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    drive = _drive("kindness-week", scope="platform", hub_id=None)
    db_session.add(drive)
    await db_session.flush()
    body = {"items": [{"code": "clothes", "qty": 1.5}], "drive_id": drive.id, "pickup_on": TUE}
    for phone in (PRIYA, SUNITA, RAMESH, MEENA):
        await donations.schedule(
            db_session, await _user(db_session, phone), DonationIn.model_validate(body), None
        )
    await db_session.commit()
    api = clients()
    priya, vikram = await _as(api, google, PRIYA), await _as(api, google, VIKRAM)

    async def seen(who: dict[str, str]) -> dict[str, Any]:
        answer = await api.get("/v1/drives", headers=who)
        return next(d for d in answer.json()["items"] if d["code"] == "kindness-week")["progress"]

    assert await seen(priya) == {"pledged": None, "verified": None}  # 4 households: under k
    assert await seen(vikram) == {"pledged": 6, "verified": 0}  # staff: exact
    await donations.schedule(
        db_session, await _user(db_session, SAVITA), DonationIn.model_validate(body), None
    )
    await db_session.commit()
    assert await seen(priya) == {"pledged": 8, "verified": 0}  # 7.5 items, half up
    rows = (await db_session.execute(select(Donation))).scalars().all()
    assert {row.estimated_points for row in rows} == {120}  # 1.5 x 40 x 2.0
