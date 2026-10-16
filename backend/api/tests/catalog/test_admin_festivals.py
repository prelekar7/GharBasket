"""The console's festival calendar (M2 §3.3): adding a manual day, editing names, rules and the
verified flag, deleting only days after today, the seed never overwriting manual, verified or
console-edited days, and that only admins do any of it."""

import uuid
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Festival
from gharbasket.modules.catalog.pricing import today
from gharbasket.modules.catalog.recipes import save_festival
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.seed import seed_catalog

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"  # customer
VIKRAM = "+919800001008"  # hub manager
NEHA = "+919800001011"  # admin
REKHA = "+919800001013"  # catalog editor
FESTIVALS = "/v1/admin/festivals"
NAMES = {"en": "Gramdevi jatra", "hi": "ग्रामदेवी जत्रा", "mr": "ग्रामदेवी जत्रा"}
RULES = {"fasting": False, "no_onion_garlic": True}


async def _as(api: httpx2.AsyncClient, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed_in["access_token"])


def _body(day: date, **overrides: Any) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "code": "gramdevi_jatra",
        "region": "maharashtra",
        "names": NAMES,
        "rules": RULES,
        **overrides,
    }


async def _create(api: httpx2.AsyncClient, auth: dict[str, str], body: dict) -> httpx2.Response:
    return await api.post(
        FESTIVALS, json=body, headers={**auth, "Idempotency-Key": uuid.uuid4().hex}
    )


async def _day(db: AsyncSession, day: date, code: str = "test_day", **more: Any) -> str:
    await save_festival(
        db,
        day=day,
        code=code,
        region="all",
        names={"en": "Test", "hi": "टेस्ट", "mr": "टेस्ट"},
        rules={"fasting": True, "no_onion_garlic": True},
        verified=more.get("verified", False),
    )
    return f"{FESTIVALS}/{day.isoformat()}/{code}/all"


def _code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["code"]


async def test_an_admin_adds_a_manual_day_that_the_app_shows(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    day = today() + timedelta(days=10)
    key = {**neha, "Idempotency-Key": uuid.uuid4().hex}

    created = await api.post(FESTIVALS, json=_body(day), headers=key)
    replayed = await api.post(FESTIVALS, json=_body(day), headers=key)

    assert created.status_code == 201, created.text
    out = created.json()
    assert out["source"] == "manual" and out["verified"] is False
    assert out["updated_by"]["name"] == "Neha Iyer" and out["names"] == NAMES
    assert replayed.json() == out
    shown = await api.get(
        "/v1/festivals", params={"from": day.isoformat(), "region": "maharashtra"}, headers=neha
    )
    assert "gramdevi_jatra" in [f["code"] for f in shown.json()["items"]]
    listed = await api.get(
        FESTIVALS,
        params={"from": day.isoformat(), "to": day.isoformat(), "region": "maharashtra"},
        headers=neha,
    )
    assert listed.json()["items"] == [out]
    [entry] = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "catalog.festival_created")
        )
    ).scalars()
    assert entry.entity_id == f"{day.isoformat()}/gramdevi_jatra/maharashtra"


async def test_a_new_day_is_checked(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    day = today() + timedelta(days=10)
    assert (await _create(api, neha, _body(day))).status_code == 201

    assert _code(await _create(api, neha, _body(day))) == (409, "catalog.festival_exists")
    missing = await _create(api, neha, _body(day, code="other", names={"en": "x", "hi": "y"}))
    assert _code(missing) == (422, "catalog.names_incomplete")
    assert missing.json()["missing"] == ["mr"]
    for invalid in (
        {"code": "Bad-Code"},
        {"code": "x"},
        {"region": "atlantis"},
        {"date": (today() + timedelta(days=800)).isoformat()},
        {"rules": {"fasting": True}},
        {"source": "panchang"},
    ):
        answer = await _create(api, neha, _body(day, **{"code": "other", **invalid}))
        assert _code(answer) == (422, "request.invalid"), invalid
    too_long = await api.get(
        FESTIVALS, params={"from": "2026-01-01", "to": "2027-01-03"}, headers=neha
    )
    assert _code(too_long) == (422, "request.invalid")


async def test_an_edit_changes_only_what_is_sent_and_the_seed_keeps_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    seeded = (
        (
            await db_session.execute(
                select(Festival).where(Festival.verified.is_(False), Festival.day > today())
            )
        )
        .scalars()
        .first()
    )
    assert seeded is not None and seeded.source == "panchang"
    path = f"{FESTIVALS}/{seeded.day.isoformat()}/{seeded.code}/{seeded.region}"
    names = dict(seeded.names)
    await db_session.commit()

    verified = await api.patch(path, json={"verified": True}, headers=neha)
    assert _code(await api.patch(path, json={"names": None}, headers=neha)) == (
        422,
        "request.invalid",
    )
    missing = f"{FESTIVALS}/{today().isoformat()}/nothing/all"
    assert _code(await api.patch(missing, json={"verified": True}, headers=neha)) == (
        404,
        "catalog.festival_not_found",
    )

    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True and verified.json()["names"] == names
    assert verified.json()["source"] == "panchang"  # still the generator's day, now checked
    [entry] = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "catalog.festival_updated")
        )
    ).scalars()
    assert entry.before == {"verified": False} and entry.after == {"verified": True}
    await seed_catalog(db_session)
    await db_session.refresh(seeded)
    assert seeded.verified is True


async def test_the_seed_refreshes_only_untouched_generated_days(
    db_session: AsyncSession,
) -> None:
    rows = list(
        (
            await db_session.execute(select(Festival).where(Festival.verified.is_(False)).limit(3))
        ).scalars()
    )
    plain, manual, edited = rows
    for row in rows:
        row.names = {**row.names, "en": "Changed"}
    manual.source = "manual"
    edited.updated_by = uuid.uuid4()
    await db_session.flush()

    await seed_catalog(db_session)

    for row in rows:
        await db_session.refresh(row)
    assert plain.names["en"] != "Changed"
    assert manual.names["en"] == "Changed" and edited.names["en"] == "Changed"


async def test_only_days_after_today_can_be_deleted(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    tomorrow = await _day(db_session, today() + timedelta(days=1))
    started = await _day(db_session, today())
    await db_session.commit()

    assert _code(await api.delete(started, headers=neha)) == (409, "catalog.festival_past")
    deleted = await api.delete(tomorrow, headers=neha)
    assert deleted.status_code == 204
    assert _code(await api.delete(tomorrow, headers=neha)) == (404, "catalog.festival_not_found")
    [entry] = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "catalog.festival_deleted")
        )
    ).scalars()
    assert entry.before is not None and entry.before["code"] == "test_day"


async def test_only_admins_edit_the_calendar(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    path = await _day(db_session, today() + timedelta(days=3))
    await db_session.commit()
    for phone in (PRIYA, VIKRAM, REKHA):
        caller = await _as(api, google, phone)
        refused = [
            await api.get(FESTIVALS, headers=caller),
            await _create(api, caller, _body(today() + timedelta(days=4))),
            await api.patch(path, json={"verified": True}, headers=caller),
            await api.delete(path, headers=caller),
        ]
        assert [_code(answer) for answer in refused] == [(403, "auth.forbidden")] * 4
