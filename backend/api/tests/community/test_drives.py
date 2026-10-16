"""Kindness point rates and donation drives (M9 §3.4): the seed rates, admin changes, who runs a
drive where, what customers and staff see, and the date rules of a change."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import drives
from gharbasket.modules.community.models import DonationDrive
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, SOCIETIES

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("kindness_world")]

PRIYA = "+919800001001"  # Sunrise Towers (seed hub)
VIKRAM = "+919800001008"  # hub manager of the seed hub
NEHA = "+919800001011"  # admin
GREEN_VALLEY = next(s.id for s in SOCIETIES if s.slug == "green-valley-heights-kharghar")
OTHER_HUB = uuid.UUID("0199a000-0000-7000-8000-00000000a0ff")
NAME = {"en": "Diwali drive", "hi": "दिवाली अभियान", "mr": "दिवाळी मोहीम"}


async def _as(api: Any, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


def _drive(code: str, **fields: Any) -> dict[str, Any]:
    today = drives.today()
    return {
        "code": code,
        "name": NAME,
        "scope": "hub",
        "hub_id": str(SEED_HUB_ID),
        "codes": ["clothes", "toys"],
        "multiplier": 2.0,
        "starts_on": today.isoformat(),
        "ends_on": (today + timedelta(days=20)).isoformat(),
        **fields,
    }


def _row(code: str, *, days: tuple[int, int], **fields: Any) -> DonationDrive:
    """A drive written straight to the table (past dates, other hubs)."""
    today = drives.today()
    values: dict[str, Any] = {
        "scope": "hub",
        "hub_id": SEED_HUB_ID,
        "codes": ["clothes"],
        "multiplier": Decimal("1.5"),
        **fields,
    }
    return DonationDrive(
        code=code,
        name={"en": code, "hi": code, "mr": code},
        starts_on=today + timedelta(days=days[0]),
        ends_on=today + timedelta(days=days[1]),
        created_by=uuid.uuid4(),
        **values,
    )


async def test_rates_are_seeded_localised_and_changed_only_by_admins(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, vikram, neha = [await _as(api, google, p) for p in (PRIYA, VIKRAM, NEHA)]
    await db_session.commit()
    answer = await api.get("/v1/point-rates", headers={**priya, "Accept-Language": "mr-IN"})
    assert answer.status_code == 200
    rates = {r["code"]: r for r in answer.json()["items"]}
    assert {c: (r["unit"], r["points"], r["max_qty"]) for c, r in rates.items()} == {
        "clothes": ("kg", 40, 15),
        "utensils": ("kg", 30, 10),
        "school_bag": ("each", 50, 5),
        "books": ("each", 10, 30),
        "toys": ("each", 15, 10),
    }
    assert rates["toys"]["label"] == "खेळणी"

    body = {"points": 45, "max_qty": 12.5, "label": {"en": "Clothes", "hi": "कपड़े", "mr": "कपडे"}}
    for who in (priya, vikram):
        refused = await api.put("/v1/admin/point-rates/clothes", json=body, headers=who)
        assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")
    changed = await api.put("/v1/admin/point-rates/clothes", json=body, headers=neha)
    assert changed.status_code == 200, changed.text
    assert (changed.json()["points"], changed.json()["max_qty"]) == (45, 12.5)
    audit = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "community.point_rate_updated")
        )
    ).scalar_one()
    assert audit.before is not None and audit.before["points"] == 40

    unknown = await api.put("/v1/admin/point-rates/sofas", json=body, headers=neha)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "community.item_code_unknown")
    half_toy = await api.put("/v1/admin/point-rates/toys", json=body, headers=neha)
    assert (half_toy.status_code, half_toy.json()["code"]) == (422, "request.invalid")


async def test_hub_managers_run_drives_in_their_hub_and_admins_anywhere(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)
    await db_session.commit()

    def post(body: dict[str, Any], who: dict[str, str], key: str) -> Any:
        return api.post("/v1/admin/drives", json=body, headers={**who, "Idempotency-Key": key})

    created = await post(_drive("diwali-2026"), vikram, "d1")
    assert created.status_code == 201, created.text
    out = created.json()
    assert (out["state"], out["multiplier"], out["version"], out["ngo"]) == (
        "running",
        2.0,
        1,
        None,
    )
    assert out["progress"] == {"pledged": 0, "verified": 0}
    assert created.headers["ETag"] == '"1"'
    replay = await post(_drive("diwali-2026"), vikram, "d1")
    assert replay.json()["id"] == out["id"]

    society = await post(
        _drive("gv-books", scope="society", hub_id=None, society_id=str(GREEN_VALLEY)),
        vikram,
        "d2",
    )
    assert society.status_code == 201
    assert society.json()["hub_id"] == str(SEED_HUB_ID)  # the society's hub

    refusals = [
        (_drive("try-1", scope="platform", hub_id=None), vikram, 403, "auth.forbidden"),
        (_drive("try-2", hub_id=str(OTHER_HUB)), vikram, 403, "auth.forbidden"),
        (_drive("try-3", hub_id=str(OTHER_HUB)), neha, 422, "request.invalid"),  # no such hub
        (_drive("try-4", codes=["sofas"]), vikram, 422, "community.item_code_unknown"),
        (_drive("diwali-2026"), vikram, 409, "community.drive_code_taken"),
        (_drive("try-5", scope="society"), vikram, 422, "request.invalid"),
        (_drive("try-6", multiplier=3.5), vikram, 422, "request.invalid"),
        (_drive("try-7", multiplier=1.25), vikram, 422, "request.invalid"),
        (_drive("try-8", codes=["toys", "toys"]), vikram, 422, "request.invalid"),
    ]
    yesterday = (drives.today() - timedelta(days=1)).isoformat()
    refusals.append(
        (_drive("try-9", starts_on=yesterday), vikram, 422, "community.drive_dates_invalid")
    )
    refusals.append(
        (_drive("try-10", ends_on=yesterday), vikram, 422, "community.drive_dates_invalid")
    )
    for n, (body, who, status, code) in enumerate(refusals):
        refused = await post(body, who, f"r{n}")
        assert (refused.status_code, refused.json()["code"]) == (status, code), refused.text

    platform = await post(_drive("kindness-week", scope="platform", hub_id=None), neha, "d3")
    assert platform.status_code == 201


async def test_customers_see_open_drives_of_their_platform_hub_and_society(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    db_session.add_all(
        [
            _row("platform-now", days=(-2, 5), scope="platform", hub_id=None),
            _row("hub-soon", days=(3, 9)),
            _row("mine", days=(0, 1), scope="society", society_id=SEED_SOCIETY_ID),
            _row("neighbours", days=(0, 1), scope="society", society_id=GREEN_VALLEY),
            _row("hub-ended", days=(-9, -1)),
            _row("hub-cancelled", days=(0, 4), cancelled_at=drives.datetime.now(drives.UTC)),
            _row("other-hub", days=(0, 4), hub_id=OTHER_HUB),
        ]
    )
    await db_session.flush()
    api = clients()
    priya, vikram, neha = [await _as(api, google, p) for p in (PRIYA, VIKRAM, NEHA)]
    await db_session.commit()

    async def codes(who: dict[str, str], query: str = "") -> list[str]:
        answer = await api.get(f"/v1/drives{query}", headers=who)
        assert answer.status_code == 200, answer.text
        return [d["code"] for d in answer.json()["items"]]

    assert await codes(priya) == ["platform-now", "mine", "hub-soon"]
    assert await codes(priya, "?state=ended") == []
    assert await codes(vikram) == ["platform-now", "mine", "neighbours", "hub-soon"]
    assert await codes(vikram, "?state=ended") == ["hub-ended"]
    assert await codes(vikram, "?state=cancelled") == ["hub-cancelled"]
    assert "other-hub" in await codes(neha)

    first = await api.get("/v1/drives?limit=2", headers=vikram)
    cursor = first.json()["next_cursor"]
    rest = await api.get(f"/v1/drives?limit=2&cursor={cursor}", headers=vikram)
    assert [d["code"] for d in first.json()["items"] + rest.json()["items"]] == await codes(vikram)
    assert rest.json()["next_cursor"] is None
    bad = await api.get("/v1/drives?cursor=e30", headers=vikram)
    assert bad.json()["code"] == "request.invalid_cursor"


async def test_a_running_drive_only_extends_and_cancelling_is_versioned(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    running = _row("running", days=(-2, 5))
    platform = _row("platform", days=(1, 5), scope="platform", hub_id=None)
    elsewhere = _row("elsewhere", days=(1, 5), hub_id=OTHER_HUB)
    upcoming = _row("upcoming", days=(2, 5))
    db_session.add_all([running, platform, elsewhere, upcoming])
    await db_session.commit()
    ids = {d: d.id for d in (running, platform, elsewhere, upcoming)}
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    today = drives.today()

    def patch(drive: DonationDrive, body: dict[str, Any], version: int | None = 1) -> Any:
        headers = vikram if version is None else {**vikram, "If-Match": f'"{version}"'}
        return api.patch(f"/v1/admin/drives/{ids[drive]}", json=body, headers=headers)

    assert (await patch(running, {"multiplier": 2.5}, None)).status_code == 428
    assert (await patch(running, {"multiplier": 2.5}, 7)).json()[
        "code"
    ] == "request.version_mismatch"
    assert (await patch(platform, {"multiplier": 2.5})).json()["code"] == "auth.forbidden"
    assert (await patch(elsewhere, {"multiplier": 2.5})).json()[
        "code"
    ] == "community.drive_not_found"
    for body in (
        {"ends_on": (today + timedelta(days=4)).isoformat()},
        {"starts_on": today.isoformat()},
    ):
        shortened = await patch(running, body)
        assert shortened.json()["code"] == "community.drive_dates_invalid"
    past = await patch(upcoming, {"starts_on": (today - timedelta(days=1)).isoformat()})
    assert past.json()["code"] == "community.drive_dates_invalid"
    assert (await patch(upcoming, {"codes": ["sofas"]})).json()["code"] == (
        "community.item_code_unknown"
    )
    assert (await patch(running, {"codes": None})).json()["code"] == "request.invalid"

    extended = await patch(
        running, {"ends_on": (today + timedelta(days=9)).isoformat(), "multiplier": 2.5}
    )
    assert extended.status_code == 200, extended.text
    assert (extended.json()["version"], extended.json()["multiplier"]) == (2, 2.5)
    assert extended.headers["ETag"] == '"2"'
    unchanged = await patch(running, {"multiplier": 2.5}, 2)
    assert unchanged.json()["version"] == 2

    cancelled = await patch(running, {"state": "cancelled"}, 2)
    assert (cancelled.json()["state"], cancelled.json()["version"]) == ("cancelled", 3)
    frozen = await patch(running, {"ends_on": (today + timedelta(days=12)).isoformat()}, 3)
    assert frozen.json()["code"] == "community.drive_dates_invalid"
    actions = (
        await db_session.execute(
            select(AuditEntry.action).where(AuditEntry.entity_id == str(ids[running]))
        )
    ).scalars()
    assert list(actions) == ["community.drive_updated", "community.drive_updated"]
