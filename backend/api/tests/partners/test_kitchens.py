"""Kitchens and capacity (M8 §3.4): the kitchen made at verification, her own reads and edits,
the hub's inspection, and capacity per date and meal with its evening-before lock."""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import kitchens, service
from gharbasket.modules.partners.models import (
    Application,
    Assignment,
    Kitchen,
    KycDocument,
    Partner,
)
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, NEHA, PRIYA, VIKRAM, as_user, other_hub_manager

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("partners_world")]

# Thursday 14 January 2027, 10:00 IST.
THURSDAY = datetime(2027, 1, 14, 4, 30, tzinfo=UTC)
FRIDAY = date(2027, 1, 15)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    at = [THURSDAY]
    monkeypatch.setattr(kitchens, "now", lambda: at[0])
    return at


async def lata_kitchen(db: AsyncSession, application: Application | None = None) -> uuid.UUID:
    """Lata (kitchen_partner role at the seed hub) verified, with her kitchen."""
    user, _ = await find_or_create_user(db, LATA)
    partner = await service.verify(
        db,
        user_id=user.id,
        kind="kitchen",
        hub_id=SEED_HUB_ID,
        display_name="Lata tai",
        application_id=None if application is None else application.id,
    )
    await kitchens.create_for(db, partner, application)
    partner_id = partner.id
    await db.commit()  # a refused request rolls the shared session back
    return partner_id


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def test_the_kitchen_takes_the_fssai_number_and_hygiene_grade_of_her_application(
    db_session: AsyncSession,
) -> None:
    user, _ = await find_or_create_user(db_session, LATA)
    approved_at = datetime(2026, 12, 20, 6, 0, tzinfo=UTC)
    application = Application(
        user_id=user.id,
        kind="kitchen",
        scope_type="hub",
        scope_id=SEED_HUB_ID,
        hub_id=SEED_HUB_ID,
        display_name="Lata tai",
        status="training",
        hygiene_grade="A",
        docs_approved_at=approved_at,
    )
    db_session.add(application)
    await db_session.flush()
    db_session.add(
        KycDocument(
            application_id=application.id,
            user_id=user.id,
            doc_type="fssai",
            fssai_reg_no="11521999000123",
            details={"valid_until": "2028-03-31"},
            check_status="passed",
            check_code="format_ok",
            checked_at=approved_at,
        )
    )
    await db_session.flush()

    partner_id = await lata_kitchen(db_session, application)

    kitchen = await db_session.get(Kitchen, partner_id)
    assert kitchen is not None
    assert (kitchen.fssai_reg_no, kitchen.fssai_valid_until) == (
        "11521999000123",
        date(2028, 3, 31),
    )
    assert (kitchen.hygiene_grade, kitchen.inspected_on) == ("A", date(2026, 12, 20))
    assert kitchen.default_portions == {"lunch": [0] * 7, "dinner": [0] * 7}
    assert kitchen.skills == [] and kitchen.location is None and kitchen.version == 1


async def test_her_kitchen_is_hers_to_read_and_edit_with_if_match(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    lata, _ = await as_user(api, google, LATA)
    priya, _ = await as_user(api, google, PRIYA)
    before = await api.get("/v1/kitchens/me", headers=lata)
    partner_id = await lata_kitchen(db_session)
    url = "/v1/kitchens/me"

    read = await api.get(url, headers=lata)
    customer = await api.get(url, headers=priya)
    no_match = await api.patch(url, json={"area": "Sector 20"}, headers=lata)
    stale = await api.patch(url, json={"area": "x"}, headers={**lata, "If-Match": '"7"'})
    bad_skill = await api.patch(
        url, json={"skills": ["veg", "keto"]}, headers={**lata, "If-Match": '"1"'}
    )
    bad_week = await api.patch(
        url,
        json={"default_portions": {"lunch": [101] * 7, "dinner": [0] * 7}},
        headers={**lata, "If-Match": '"1"'},
    )
    short_week = await api.patch(
        url, json={"default_portions": {"lunch": [1] * 6}}, headers={**lata, "If-Match": '"1"'}
    )

    assert code(before) == (404, "partners.kitchen_not_found")
    assert read.status_code == 200, read.text
    assert read.headers["ETag"] == '"1"' and read.headers["Cache-Control"] == "no-store"
    body = read.json()
    assert body["partner_id"] == str(partner_id) and body["display_name"] == "Lata tai"
    assert (body["status"], body["location"], body["hygiene"], body["skills"]) == (
        "active",
        None,
        None,
        [],
    )
    assert code(customer) == (403, "auth.forbidden")
    assert code(no_match) == (428, "request.precondition_required")
    assert code(stale) == (412, "request.version_mismatch")
    assert code(bad_skill) == (422, "partners.skills_invalid")
    assert bad_skill.json()["skills"] == ["keto"]
    assert code(bad_week) == code(short_week) == (422, "partners.capacity_invalid")

    edited = await api.patch(
        url,
        json={
            "display_name": "Lata's kitchen",
            "area": "  Sector 20,  Kharghar ",
            "address": "B-1103, Sunrise Towers",
            "location": {"lat": 19.0412, "lng": 73.0661},
            "skills": ["jain", "veg", "jain"],
            "default_portions": {"lunch": [10, 10, 10, 10, 10, 8, 0], "dinner": [0] * 7},
        },
        headers={**lata, "If-Match": '"1"'},
    )

    assert edited.status_code == 200, edited.text
    assert edited.headers["ETag"] == '"2"'
    kitchen = edited.json()
    assert kitchen["display_name"] == "Lata's kitchen" and kitchen["area"] == "Sector 20, Kharghar"
    assert kitchen["location"] == {"lat": pytest.approx(19.0412), "lng": pytest.approx(73.0661)}
    assert kitchen["skills"] == ["veg", "jain"]
    assert kitchen["default_portions"]["lunch"] == [10, 10, 10, 10, 10, 8, 0]
    partner = await db_session.get(Partner, partner_id)
    assert kitchen["version"] == 2 and partner is not None
    assert partner.display_name == "Lata's kitchen"
    cleared = await api.patch(
        url, json={"location": None, "display_name": None}, headers={**lata, "If-Match": '"2"'}
    )
    assert cleared.json()["location"] is None and cleared.json()["display_name"] == "Lata's kitchen"


async def test_hub_staff_record_an_inspection_once_per_key_and_it_is_audited(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, vikram_id = await as_user(api, google, VIKRAM)
    neha, _ = await as_user(api, google, NEHA)
    priya, _ = await as_user(api, google, PRIYA)
    stranger = await other_hub_manager(api, google, db_session)
    partner_id = await lata_kitchen(db_session)
    url = f"/v1/kitchens/{partner_id}/inspection"
    body = {"grade": "B", "note": " Drain  needs a cover "}

    graded = await api.post(url, json=body, headers={**vikram, "Idempotency-Key": "i1"})
    replayed = await api.post(url, json=body, headers={**vikram, "Idempotency-Key": "i1"})
    other_hub = await api.post(url, json=body, headers={**stranger, "Idempotency-Key": "i2"})
    customer = await api.post(url, json=body, headers={**priya, "Idempotency-Key": "i3"})
    unknown = await api.post(
        f"/v1/kitchens/{uuid.uuid4()}/inspection",
        json=body,
        headers={**neha, "Idempotency-Key": "i4"},
    )
    bad_grade = await api.post(url, json={"grade": "D"}, headers={**neha, "Idempotency-Key": "i5"})

    assert graded.status_code == 200, graded.text
    assert graded.json()["hygiene"] == {"grade": "B", "inspected_on": "2027-01-14"}
    assert graded.json()["address"] is None  # hub staff may see it; she has not set it yet
    assert replayed.headers.get("Idempotent-Replayed") == "true"
    assert code(other_hub) == code(unknown) == (404, "partners.kitchen_not_found")
    assert code(customer) == (403, "auth.forbidden")
    assert bad_grade.status_code == 422
    entry = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "partners.kitchen_inspected")
        )
    ).scalar_one()
    assert entry.actor_user_id == vikram_id and entry.entity_id == str(partner_id)
    assert entry.before == {"grade": None, "inspected_on": None}
    assert entry.after == {
        "grade": "B",
        "inspected_on": "2027-01-14",
        "note": "Drain needs a cover",
    }


async def test_capacity_is_the_weekday_default_unless_set_and_closes_at_21_the_day_before(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    lata, _ = await as_user(api, google, LATA)
    partner_id = await lata_kitchen(db_session)
    kitchen = await db_session.get(Kitchen, partner_id)
    assert kitchen is not None
    kitchen.default_portions = {
        "lunch": [10, 10, 10, 10, 10, 8, 0],
        "dinner": [0, 0, 0, 0, 4, 0, 0],
    }
    await db_session.flush()

    week = await api.get("/v1/kitchen-capacity", headers=lata)
    assert week.status_code == 200, week.text
    items = week.json()["items"]
    assert len(items) == 14 and items[0]["date"] == "2027-01-14"
    assert [(i["date"], i["meal"]) for i in items[:2]] == [
        ("2027-01-14", "lunch"),
        ("2027-01-14", "dinner"),
    ]
    friday = {i["meal"]: i for i in items if i["date"] == "2027-01-15"}
    assert friday["lunch"] == {
        "date": "2027-01-15",
        "meal": "lunch",
        "max_portions": 10,
        "source": "default",
        "assigned_portions": 0,
        "locked": False,
    }
    assert friday["dinner"]["max_portions"] == 4
    assert {i["locked"] for i in items if i["date"] == "2027-01-14"} == {True}

    put = await api.put(
        "/v1/kitchen-capacity",
        json={"date": "2027-01-15", "meal": "lunch", "max_portions": 12},
        headers=lata,
    )
    again = await api.put(
        "/v1/kitchen-capacity",
        json={"date": "2027-01-15", "meal": "lunch", "max_portions": 0},
        headers=lata,
    )
    assert put.status_code == 200, put.text
    assert (put.json()["max_portions"], put.json()["source"]) == (12, "set")
    assert again.json()["max_portions"] == 0
    read = await api.get("/v1/kitchen-capacity?from=2027-01-15&to=2027-01-15", headers=lata)
    assert [(i["meal"], i["max_portions"], i["source"]) for i in read.json()["items"]] == [
        ("lunch", 0, "set"),
        ("dinner", 4, "default"),
    ]

    invalid = [
        {"date": "2027-01-15", "meal": "lunch", "max_portions": 101},
        {"date": "2027-01-15", "meal": "lunch", "max_portions": -1},
        {"date": "2027-01-15", "meal": "brunch", "max_portions": 5},
        {"date": "2027-01-13", "meal": "lunch", "max_portions": 5},
        {"date": "2027-01-28", "meal": "lunch", "max_portions": 5},
    ]
    for body in invalid:
        answer = await api.put("/v1/kitchen-capacity", json=body, headers=lata)
        assert code(answer) == (422, "partners.capacity_invalid"), body
    for query in (
        "from=2027-01-13",
        "from=2027-01-14&to=2027-01-28",
        "from=2027-01-16&to=2027-01-15",
        "from=9999-12-30",
    ):
        answer = await api.get(f"/v1/kitchen-capacity?{query}", headers=lata)
        assert code(answer) == (422, "partners.capacity_invalid"), query

    clock[0] = THURSDAY.replace(hour=15, minute=30)  # 21:00 IST
    locked = await api.put(
        "/v1/kitchen-capacity",
        json={"date": "2027-01-15", "meal": "dinner", "max_portions": 2},
        headers=lata,
    )
    saturday = await api.put(
        "/v1/kitchen-capacity",
        json={"date": "2027-01-16", "meal": "dinner", "max_portions": 2},
        headers=lata,
    )
    assert code(locked) == (409, "partners.capacity_locked")
    assert saturday.status_code == 200
    after = await api.get("/v1/kitchen-capacity?from=2027-01-15&to=2027-01-16", headers=lata)
    assert [i["locked"] for i in after.json()["items"]] == [True, True, False, False]
    assert kitchens.locks_at(FRIDAY) == THURSDAY + timedelta(hours=11)


async def test_assigned_portions_count_her_live_assignments_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    lata, _ = await as_user(api, google, LATA)
    partner_id = await lata_kitchen(db_session)
    for status, portions in (("offered", 5), ("accepted", 2), ("declined", 3), ("expired", 4)):
        db_session.add(
            Assignment(
                partner_id=partner_id,
                hub_id=SEED_HUB_ID,
                on_date=FRIDAY,
                meal="lunch",
                slot_id=uuid.uuid4(),
                round=1,
                source="fallback",
                status=status,
                portions=portions,
                respond_by=THURSDAY,
            )
        )
    await db_session.commit()

    read = await api.get("/v1/kitchen-capacity?from=2027-01-15&to=2027-01-15", headers=lata)

    assert [(i["meal"], i["assigned_portions"]) for i in read.json()["items"]] == [
        ("lunch", 7),
        ("dinner", 0),
    ]
