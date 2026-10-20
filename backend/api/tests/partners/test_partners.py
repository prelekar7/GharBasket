"""The partner core (M8 §3.3): verification, own records, hub staff's list and profile, suspend
and reinstate, pause and resume, and the facade."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import service
from gharbasket.modules.partners.models import Partner
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, MEENA, NEHA, PRIYA, SANJAY, SAVITA, VIKRAM, as_user, other_hub_manager

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("partners_world")]

STAFF_FIELDS = {"name", "phone_e164", "application_id", "requested_society"}


async def _events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


async def _user_id(db: AsyncSession, phone: str) -> uuid.UUID:
    user, _ = await find_or_create_user(db, phone)
    return user.id


async def _kitchen(db: AsyncSession) -> Partner:
    """Lata as a verified kitchen partner of the seed hub."""
    return await service.verify(
        db,
        user_id=await _user_id(db, LATA),
        kind="kitchen",
        hub_id=SEED_HUB_ID,
        display_name="Lata tai",
    )


async def test_verification_publishes_the_society_for_captains_only(
    db_session: AsyncSession,
) -> None:
    kitchen = await service.verify(
        db_session,
        user_id=await _user_id(db_session, LATA),
        kind="kitchen",
        hub_id=SEED_HUB_ID,
        display_name="Lata tai",
        requested_society_id=SEED_SOCIETY_ID,  # ignored: only captains ask for a society
    )

    verified = await _events(db_session, partners_api.VERIFIED)
    assert verified[-1] == {
        "partner_id": str(kitchen.id),
        "user_id": str(kitchen.user_id),
        "kind": "kitchen",
        "hub_id": str(SEED_HUB_ID),
        "requested_society_id": None,
    }
    meena = await partners_api.partner_for_user(
        db_session, await _user_id(db_session, MEENA), "captain"
    )
    assert meena is not None
    assert {
        "partner_id": str(meena.id),
        "user_id": str(meena.user_id),
        "kind": "captain",
        "hub_id": str(SEED_HUB_ID),
        "requested_society_id": str(SEED_SOCIETY_ID),
    } in verified
    assert kitchen.status == "active" and kitchen.version == 1


async def test_one_record_per_person_and_kind_and_a_partner_who_left_comes_back_on_it(
    db_session: AsyncSession,
) -> None:
    kitchen = await _kitchen(db_session)

    with pytest.raises(ProblemError) as again:
        await _kitchen(db_session)
    assert again.value.code == "partners.already_applied"
    assert again.value.extra == {"partner_id": str(kitchen.id)}

    kitchen.status = "left"
    await db_session.flush()
    back = await _kitchen(db_session)
    assert (back.id, back.status, back.version) == (kitchen.id, "active", 2)
    # Another kind is a new record.
    rider = await service.verify(
        db_session, user_id=kitchen.user_id, kind="rider", hub_id=SEED_HUB_ID, display_name="Lata"
    )
    assert rider.id != kitchen.id


async def test_my_partner_records_show_no_staff_details(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    meena, meena_id = await as_user(api, google, MEENA)
    priya, _ = await as_user(api, google, PRIYA)

    mine = await api.get("/v1/partners/me", headers=meena)
    nothing = await api.get("/v1/partners/me", headers=priya)

    assert mine.status_code == 200, mine.text
    assert mine.headers["Cache-Control"] == "no-store"
    (record,) = mine.json()["items"]
    assert record["user_id"] == str(meena_id)
    assert (record["kind"], record["status"], record["display_name"]) == (
        "captain",
        "active",
        "Meena tai",
    )
    assert record["photo_url"] is None and record["rating"] is None
    assert record["suspension"] is None and record["version"] == 1
    assert not STAFF_FIELDS & set(record)
    assert nothing.json() == {"items": []}


async def test_hub_staff_list_their_hubs_partners_by_name(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    sanjay, _ = await as_user(api, google, SANJAY)
    await _kitchen(db_session)
    url = f"/v1/hubs/{SEED_HUB_ID}/partners"

    every = await api.get(url, headers=vikram)
    captains = await api.get(url, params={"kind": "captain"}, headers=sanjay)
    page = await api.get(url, params={"limit": 2}, headers=vikram)
    rest = await api.get(
        url, params={"limit": 2, "cursor": page.json()["next_cursor"]}, headers=vikram
    )
    paused = await api.get(url, params={"status": "paused"}, headers=vikram)

    assert every.status_code == 200, every.text
    names = [p["display_name"] for p in every.json()["items"]]
    assert names == ["Lata tai", "Meena tai", "Savita tai"]
    meena = every.json()["items"][1]
    assert (meena["name"], meena["phone_e164"]) == ("Meena Jadhav", MEENA)
    assert meena["application_id"] is None and meena["requested_society"] is None
    assert [p["display_name"] for p in captains.json()["items"]] == ["Meena tai", "Savita tai"]
    assert [p["display_name"] for p in page.json()["items"]] == names[:2]
    assert [p["display_name"] for p in rest.json()["items"]] == names[2:]
    assert rest.json()["next_cursor"] is None
    assert paused.json()["items"] == []


async def test_other_hubs_and_customers_cannot_list_or_read(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    stranger = await other_hub_manager(api, google, db_session)
    vikram, _ = await as_user(api, google, VIKRAM)
    meena = await partners_api.partner_for_user(
        db_session, await _user_id(db_session, MEENA), "captain"
    )
    assert meena is not None
    await db_session.commit()  # the refusals below roll the shared session back

    listed = await api.get(f"/v1/hubs/{SEED_HUB_ID}/partners", headers=stranger)
    by_customer = await api.get(f"/v1/hubs/{SEED_HUB_ID}/partners", headers=priya)
    read = await api.get(f"/v1/partners/{meena.id}", headers=stranger)
    unknown = await api.get(f"/v1/partners/{uuid.uuid4()}", headers=vikram)
    bad_cursor = await api.get(
        f"/v1/hubs/{SEED_HUB_ID}/partners", params={"cursor": "eyJhIjoxfQ"}, headers=vikram
    )

    assert listed.status_code == by_customer.status_code == 403
    assert listed.json()["code"] == "auth.forbidden"
    assert read.status_code == unknown.status_code == 404
    assert read.json()["code"] == "partners.partner_not_found"
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["code"] == "request.invalid_cursor"


async def test_suspend_and_reinstate_publish_events_and_are_audited(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, vikram_id = await as_user(api, google, VIKRAM)
    neha, _ = await as_user(api, google, NEHA)
    savita = await partners_api.partner_for_user(
        db_session, await _user_id(db_session, SAVITA), "captain"
    )
    assert savita is not None
    body = {"reason_code": "conduct", "note": "  Missed  three handovers "}
    key = {**vikram, "Idempotency-Key": "suspend-savita"}

    suspended = await api.post(f"/v1/partners/{savita.id}/suspend", json=body, headers=key)
    replayed = await api.post(f"/v1/partners/{savita.id}/suspend", json=body, headers=key)
    profile = await api.get(f"/v1/partners/{savita.id}", headers=neha)
    own = await api.get("/v1/partners/me", headers=(await as_user(api, google, SAVITA))[0])

    assert suspended.status_code == 200, suspended.text
    assert replayed.headers.get("Idempotent-Replayed") == "true"
    staff = suspended.json()
    assert staff["status"] == "suspended" and staff["version"] == 2
    assert staff["suspension"]["reason_code"] == "conduct"
    assert staff["suspension"]["note"] == "Missed three handovers"
    assert profile.json()["suspension"] == staff["suspension"]
    # The note is staff-only: the partner sees why in general, not what staff wrote.
    assert own.json()["items"][0]["suspension"]["note"] is None
    assert own.json()["items"][0]["suspension"]["reason_code"] == "conduct"
    assert await _events(db_session, partners_api.SUSPENDED) == [
        {
            "partner_id": str(savita.id),
            "user_id": str(savita.user_id),
            "kind": "captain",
            "hub_id": str(SEED_HUB_ID),
            "reason_code": "conduct",
        }
    ]

    reinstated = await api.post(
        f"/v1/partners/{savita.id}/reinstate",
        json={"note": "Talked it through"},
        headers={**vikram, "Idempotency-Key": "reinstate-savita"},
    )
    assert reinstated.status_code == 200, reinstated.text
    assert reinstated.json()["status"] == "active" and reinstated.json()["suspension"] is None
    assert await _events(db_session, partners_api.REINSTATED) == [
        {
            "partner_id": str(savita.id),
            "user_id": str(savita.user_id),
            "kind": "captain",
            "hub_id": str(SEED_HUB_ID),
        }
    ]
    audits = (
        (
            await db_session.execute(
                select(AuditEntry)
                .where(AuditEntry.entity_type == "partner", AuditEntry.entity_id == str(savita.id))
                .order_by(AuditEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(a.action, a.actor_user_id) for a in audits] == [
        ("partners.suspended", vikram_id),
        ("partners.reinstated", vikram_id),
    ]
    assert audits[0].after == {
        "status": "suspended",
        "reason_code": "conduct",
        "note": "Missed three handovers",
    }


async def test_suspend_twice_reinstate_active_and_who_may(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    sanjay, _ = await as_user(api, google, SANJAY)
    stranger = await other_hub_manager(api, google, db_session)
    meena = await partners_api.partner_for_user(
        db_session, await _user_id(db_session, MEENA), "captain"
    )
    assert meena is not None
    url = f"/v1/partners/{meena.id}"
    body = {"reason_code": "other", "note": "Moved away"}
    first = await api.post(f"{url}/suspend", json=body, headers={**vikram, "Idempotency-Key": "a"})
    assert first.status_code == 200, first.text
    await db_session.commit()

    twice = await api.post(f"{url}/suspend", json=body, headers={**vikram, "Idempotency-Key": "b"})
    by_owner = await api.post(
        f"{url}/suspend", json=body, headers={**sanjay, "Idempotency-Key": "c"}
    )
    by_stranger = await api.post(
        f"{url}/reinstate", json={}, headers={**stranger, "Idempotency-Key": "d"}
    )
    no_note = await api.post(
        f"{url}/suspend",
        json={"reason_code": "other", "note": "   "},
        headers={**vikram, "Idempotency-Key": "e"},
    )
    left = await api.post(
        f"{url}/suspend",
        json={"reason_code": "left", "note": "x"},
        headers={**vikram, "Idempotency-Key": "f"},
    )
    back = await api.post(f"{url}/reinstate", json={}, headers={**vikram, "Idempotency-Key": "g"})
    await db_session.commit()
    active = await api.post(f"{url}/reinstate", json={}, headers={**vikram, "Idempotency-Key": "h"})

    assert (twice.status_code, twice.json()["code"]) == (409, "partners.already_suspended")
    assert by_owner.status_code == 403  # franchise owners read; they do not suspend
    assert by_stranger.status_code == 404
    assert no_note.status_code == left.status_code == 422
    assert back.status_code == 200, back.text
    assert (active.status_code, active.json()["code"]) == (409, "partners.not_suspended")


async def test_kitchens_pause_and_resume_but_captains_cannot(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    lata, _ = await as_user(api, google, LATA)
    meena, _ = await as_user(api, google, MEENA)
    kitchen = await _kitchen(db_session)
    captain = await partners_api.partner_for_user(
        db_session, await _user_id(db_session, MEENA), "captain"
    )
    assert captain is not None
    await db_session.commit()

    paused = await api.post(
        f"/v1/partners/{kitchen.id}/pause", headers={**lata, "Idempotency-Key": "p1"}
    )
    paused_again = await api.post(
        f"/v1/partners/{kitchen.id}/pause", headers={**lata, "Idempotency-Key": "p2"}
    )
    resumed = await api.post(
        f"/v1/partners/{kitchen.id}/resume", headers={**lata, "Idempotency-Key": "p3"}
    )
    await db_session.commit()
    not_hers = await api.post(
        f"/v1/partners/{kitchen.id}/pause", headers={**meena, "Idempotency-Key": "p4"}
    )
    a_captain = await api.post(
        f"/v1/partners/{captain.id}/pause", headers={**meena, "Idempotency-Key": "p5"}
    )

    assert paused.status_code == 200, paused.text
    assert (paused.json()["status"], paused.json()["version"]) == ("paused", 2)
    assert (paused_again.json()["status"], paused_again.json()["version"]) == ("paused", 2)
    assert (resumed.json()["status"], resumed.json()["version"]) == ("active", 3)
    assert (not_hers.status_code, not_hers.json()["code"]) == (404, "partners.partner_not_found")
    assert (a_captain.status_code, a_captain.json()["code"]) == (409, "partners.pause_not_allowed")
    assert not await _events(db_session, partners_api.SUSPENDED)  # pausing changes no roles


async def test_a_suspended_partner_cannot_pause_or_resume(db_session: AsyncSession) -> None:
    kitchen = await _kitchen(db_session)
    await service.suspend(db_session, kitchen, reason_code="hygiene", note="x", by_user_id=None)

    for paused in (True, False):
        with pytest.raises(ProblemError) as refused:
            await service.set_paused(db_session, kitchen, paused=paused)
        assert refused.value.code == "partners.pause_not_allowed"


async def test_the_facade_and_the_export(db_session: AsyncSession) -> None:
    meena_id = await _user_id(db_session, MEENA)
    ref = await partners_api.partner_for_user(db_session, meena_id, "captain")
    assert ref is not None
    partner = await db_session.get(Partner, ref.id)
    assert partner is not None
    await service.suspend(
        db_session, partner, reason_code="conduct", note="staff only", by_user_id=None
    )

    assert await partners_api.get_partner(db_session, ref.id) == partners_api.PartnerRef(
        id=ref.id,
        user_id=meena_id,
        kind="captain",
        hub_id=SEED_HUB_ID,
        status="suspended",
        display_name="Meena tai",
    )
    assert await partners_api.get_partner(db_session, uuid.uuid4()) is None
    assert await partners_api.partner_for_user(db_session, meena_id, "kitchen") is None
    assert await partners_api.profile_card(db_session, ref.id) == partners_api.ProfileCard(
        "Meena tai", None
    )
    assert await partners_api.profile_card(db_session, uuid.uuid4()) is None
    exported = await partners_api.export_for_user(db_session, meena_id)
    (record,) = exported["partners"]
    assert record["kind"] == "captain" and record["suspend_reason_code"] == "conduct"
    assert "suspend_note" not in record and "photo_key" not in record
    assert await partners_api.export_for_user(db_session, uuid.uuid4()) == {
        "applications": [],
        "kyc_documents": [],
        "training_attempts": [],
        "partners": [],
    }
