"""Applications (M8 §3.1): apply from the household, the hub's rider and packer applications,
one open application per person, withdrawal, the review queue and the data export."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Household
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import service
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    LATA,
    MEENA,
    NEHA,
    PRIYA,
    RAMESH,
    SUNITA,
    VIKRAM,
    as_user,
    other_hub_manager,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("partners_world")]

KRISHNA_KUNJ = uuid.UUID("0199a000-0000-7000-8000-00000000b005")
KITCHEN_DOCUMENTS = ["id_proof", "pan", "bank", "fssai", "kitchen_photos"]
HUB_URL = f"/v1/hubs/{SEED_HUB_ID}/partner-applications"


async def post(
    api: httpx2.AsyncClient, url: str, auth: dict[str, str], body: Any = None, key: str = ""
) -> httpx2.Response:
    return await api.post(
        url, json=body or {}, headers={**auth, "Idempotency-Key": key or uuid.uuid4().hex}
    )


async def apply(
    api: httpx2.AsyncClient, auth: dict[str, str], key: str = "", **body: Any
) -> httpx2.Response:
    return await post(api, "/v1/partner-applications", auth, body, key)


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def _events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


async def test_a_customer_applies_from_her_household_and_the_key_replays(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)

    first = await apply(api, priya, key="k-apply", kind="kitchen")
    again = await apply(api, priya, key="k-apply", kind="kitchen")
    mine = await api.get("/v1/partner-applications/me", headers=priya)

    assert first.status_code == 201, first.text
    body = first.json()
    assert (body["kind"], body["status"], body["scope_type"]) == ("kitchen", "applied", "hub")
    assert body["scope_id"] == body["hub_id"] == str(SEED_HUB_ID)
    assert body["display_name"] == "Priya Sharma"  # her M1 name
    assert body["next_step"] == "documents"
    assert [d["doc_type"] for d in body["requirements"]["documents"]] == KITCHEN_DOCUMENTS
    assert {d["status"] for d in body["requirements"]["documents"]} == {"missing"}
    assert body["requirements"]["trainings"] == [
        {"code": "app_basics", "status": "todo"},
        {"code": "hygiene", "status": "todo"},
        {"code": "kitchen_ops", "status": "todo"},
    ]
    assert body["partner_id"] is None and body["reason"] is None and body["version"] == 1
    assert again.status_code == 201 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == body["id"]
    assert mine.status_code == 200 and mine.json() == body
    assert mine.headers["Cache-Control"] == "no-store"
    assert await _events(db_session, partners_api.APPLICATION_SUBMITTED) == [
        {
            "application_id": body["id"],
            "user_id": str(priya_id),
            "kind": "kitchen",
            "hub_id": str(SEED_HUB_ID),
            "scope_type": "hub",
            "scope_id": str(SEED_HUB_ID),
        }
    ]


async def test_a_captain_applies_for_the_society_she_lives_in_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    ramesh, _ = await as_user(api, google, RAMESH)
    sunita, _ = await as_user(api, google, SUNITA)
    await db_session.commit()

    elsewhere = await apply(api, ramesh, kind="captain", scope_id=str(SEED_SOCIETY_ID))
    other_hub = await apply(api, sunita, kind="rider", scope_id=str(uuid.uuid4()))
    own = await apply(api, ramesh, kind="captain", display_name="  Ramesh   kaka ")

    assert code(elsewhere) == (422, "partners.captain_not_resident")
    assert code(other_hub) == (422, "partners.scope_invalid")
    assert own.status_code == 201, own.text
    body = own.json()
    assert (body["scope_type"], body["scope_id"]) == ("society", str(KRISHNA_KUNJ))
    assert body["hub_id"] == str(SEED_HUB_ID) and body["display_name"] == "Ramesh kaka"
    # A captain needs no society proof: residency is her household's society (owner, 2026-10-09).
    assert [d["doc_type"] for d in body["requirements"]["documents"]] == ["id_proof", "pan", "bank"]
    assert [t["code"] for t in body["requirements"]["trainings"]] == [
        "app_basics",
        "captain_handover",
    ]


async def test_the_household_hub_kind_and_name_are_checked(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    lata, _ = await as_user(api, google, LATA)
    await db_session.commit()

    unknown = await apply(api, priya, kind="chef")
    no_household = await apply(api, lata, kind="kitchen")
    short = await apply(api, priya, kind="packer", display_name=" A ")
    long = await apply(api, priya, kind="packer", display_name="x" * 41)
    await db_session.execute(
        update(Household).where(Household.owner_user_id == priya_id).values(hub_id=None)
    )
    await db_session.commit()
    no_hub = await apply(api, priya, kind="packer")

    assert code(unknown) == (422, "partners.kind_invalid")
    assert code(no_household) == (422, "partners.household_required")
    assert code(short) == code(long) == (422, "partners.display_name_invalid")
    assert code(no_hub) == (422, "partners.no_hub")
    assert await _events(db_session, partners_api.APPLICATION_SUBMITTED) == []


async def test_one_open_application_per_person_and_another_kind_after_verification(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)
    first = await apply(api, priya, kind="kitchen")
    await db_session.commit()

    second = await apply(api, priya, kind="rider")
    captain_again = await apply(api, meena, kind="captain")
    kitchen = await apply(api, meena, kind="kitchen")

    assert code(second) == (409, "partners.already_applied")
    assert second.json()["application_id"] == first.json()["id"]
    assert code(captain_again) == (409, "partners.already_applied")
    assert "partner_id" in captain_again.json()
    # A verified captain may apply for another kind.
    assert kitchen.status_code == 201, kitchen.text


async def test_withdrawal_closes_the_application_and_the_person_may_apply_again(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    sunita, _ = await as_user(api, google, SUNITA)
    application = (await apply(api, priya, kind="packer")).json()
    url = f"/v1/partner-applications/{application['id']}/withdraw"

    by_other = await post(api, url, sunita)
    await db_session.commit()
    withdrawn = await post(api, url, priya)
    await db_session.commit()
    again = await post(api, url, priya)
    latest = await api.get("/v1/partner-applications/me", headers=priya)
    nothing = await api.get("/v1/partner-applications/me", headers=sunita)
    reapplied = await apply(api, priya, kind="packer")

    assert code(by_other) == (404, "partners.application_not_found")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    assert withdrawn.json()["next_step"] is None and withdrawn.json()["version"] == 2
    assert code(again) == (409, "partners.application_closed")
    assert latest.json()["id"] == application["id"]
    assert code(nothing) == (404, "partners.application_not_found")
    assert reapplied.status_code == 201 and reapplied.json()["id"] != application["id"]


async def test_a_hub_manager_creates_a_rider_application_for_a_person_without_a_household(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, vikram_id = await as_user(api, google, VIKRAM)
    arjun, arjun_id = await as_user(api, google, ARJUN)
    stranger = await other_hub_manager(api, google, db_session)
    await db_session.commit()

    kitchen = await post(
        api, HUB_URL, vikram, {"kind": "kitchen", "phone_e164": ARJUN, "display_name": "Arjun"}
    )
    unknown = await post(
        api,
        HUB_URL,
        vikram,
        {"kind": "rider", "phone_e164": "+919811111111", "display_name": "Ravi"},
    )
    other_hub = await post(
        api, HUB_URL, stranger, {"kind": "rider", "phone_e164": ARJUN, "display_name": "Arjun"}
    )
    created = await post(
        api, HUB_URL, vikram, {"kind": "rider", "phone_e164": ARJUN, "display_name": "Arjun bhai"}
    )
    mine = await api.get("/v1/partner-applications/me", headers=arjun)

    assert code(kitchen) == (422, "partners.kind_invalid")
    assert code(unknown) == (422, "partners.user_not_found")
    assert code(other_hub) == (403, "auth.forbidden")
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["kind"], body["scope_type"], body["scope_id"]) == (
        "rider",
        "hub",
        str(SEED_HUB_ID),
    )
    assert body["display_name"] == "Arjun bhai" and "applicant" not in body
    assert mine.json()["id"] == body["id"]
    audit = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "partners.application_created")
        )
    ).scalar_one()
    assert (audit.actor_user_id, audit.entity_id) == (vikram_id, body["id"])
    assert audit.after == {"user_id": str(arjun_id), "kind": "rider", "hub_id": str(SEED_HUB_ID)}


async def test_the_review_queue_and_the_review_view_are_for_that_hubs_staff(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    neha, _ = await as_user(api, google, NEHA)
    priya, _ = await as_user(api, google, PRIYA)
    ramesh, ramesh_id = await as_user(api, google, RAMESH)
    stranger = await other_hub_manager(api, google, db_session)
    kitchen = (await apply(api, priya, kind="kitchen")).json()
    captain = (await apply(api, ramesh, kind="captain")).json()
    await db_session.commit()

    pending = await api.get(HUB_URL, headers=vikram)
    applied = await api.get(HUB_URL, params={"status": "applied"}, headers=neha)
    page = await api.get(HUB_URL, params={"status": "applied", "limit": 1}, headers=vikram)
    rest = await api.get(
        HUB_URL,
        params={"status": "applied", "limit": 1, "cursor": page.json()["next_cursor"]},
        headers=vikram,
    )
    captains = await api.get(
        HUB_URL, params={"status": "applied", "kind": "captain"}, headers=vikram
    )
    review = await api.get(f"/v1/partner-applications/{captain['id']}", headers=vikram)
    own = await api.get(f"/v1/partner-applications/{captain['id']}", headers=ramesh)
    by_stranger = await api.get(f"/v1/partner-applications/{captain['id']}", headers=stranger)
    by_customer = await api.get(f"/v1/partner-applications/{captain['id']}", headers=priya)
    queue_stranger = await api.get(HUB_URL, headers=stranger)
    queue_customer = await api.get(HUB_URL, headers=priya)

    assert pending.status_code == 200, pending.text
    assert pending.json() == {"items": [], "next_cursor": None}  # status defaults to kyc_pending
    assert [a["id"] for a in applied.json()["items"]] == [kitchen["id"], captain["id"]]
    assert [a["id"] for a in page.json()["items"] + rest.json()["items"]] == [
        kitchen["id"],
        captain["id"],
    ]
    assert rest.json()["next_cursor"] is None
    assert [a["id"] for a in captains.json()["items"]] == [captain["id"]]
    assert review.status_code == 200, review.text
    staff_view = review.json()
    assert staff_view["applicant"] == {
        "user_id": str(ramesh_id),
        "name": "Ramesh Kulkarni",
        "phone_e164": RAMESH,
    }
    assert staff_view["society"]["id"] == str(KRISHNA_KUNJ)
    assert staff_view["society"]["name"] == "Krishna Kunj"
    assert applied.json()["items"][0]["society"] is None  # not a captain
    assert own.status_code == 200 and "applicant" not in own.json()
    assert code(by_stranger) == code(by_customer) == (404, "partners.application_not_found")
    assert code(queue_stranger) == code(queue_customer) == (403, "auth.forbidden")


async def test_a_verified_captains_staff_view_shows_her_requested_society(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    ramesh, ramesh_id = await as_user(api, google, RAMESH)
    application = (await apply(api, ramesh, kind="captain")).json()
    partner = await service.verify(
        db_session,
        user_id=ramesh_id,
        kind="captain",
        hub_id=SEED_HUB_ID,
        display_name="Ramesh kaka",
        application_id=uuid.UUID(application["id"]),
        requested_society_id=KRISHNA_KUNJ,
    )

    staff = await api.get(f"/v1/partners/{partner.id}", headers=vikram)

    assert staff.json()["application_id"] == application["id"]
    assert staff.json()["requested_society"] == {"id": str(KRISHNA_KUNJ), "name": "Krishna Kunj"}


async def test_the_data_export_has_her_applications_without_staff_fields(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await post(
        api, HUB_URL, vikram, {"kind": "packer", "phone_e164": ARJUN, "display_name": "Arjun"}
    )
    arjun, _ = await find_or_create_user(db_session, ARJUN)

    section = await partners_api.export_for_user(db_session, arjun.id)

    (application,) = section["applications"]
    assert (application["kind"], application["status"]) == ("packer", "applied")
    assert not {"created_by", "decided_by", "data_source"} & set(application)
    assert section["partners"] == []
