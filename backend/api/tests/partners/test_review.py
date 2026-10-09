"""The hub's decision (M8 §3.1) and verification: approve, resubmit and reject; approval and the
last training pass, whichever comes last, verify once; M1's hub roles follow the partner events."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import partner_roles  # noqa: F401 (registers the consumers)
from gharbasket.modules.identity.models import UserRole
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import service
from gharbasket.modules.partners.models import Application, Partner
from gharbasket.platform.models import AuditEntry, OutboxEvent, Upload
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..test_files import FakeS3
from .conftest import LATA, NEHA, PRIYA, RAMESH, VIKRAM, as_user, other_hub_manager
from .test_applications import HUB_URL, apply, code, post
from .test_kyc import BANK, VOTER_ID, send, store_for, upload_key
from .test_training import answers, complete

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("partners_world")]

KRISHNA_KUNJ = "0199a000-0000-7000-8000-00000000b005"
CAPTAIN_DOCS = [VOTER_ID, {"doc_type": "pan", "number": "ABCDE1234F"}, BANK]


async def decide(
    api: httpx2.AsyncClient, auth: dict[str, str], application_id: str, **body: Any
) -> httpx2.Response:
    return await post(api, f"/v1/partner-applications/{application_id}/decision", auth, body)


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


async def ready_for_review(
    api: httpx2.AsyncClient, auth: dict[str, str], kind: str, docs: list[dict[str, Any]]
) -> str:
    application = (await apply(api, auth, kind=kind)).json()["id"]
    for doc in docs:
        assert (await send(api, auth, application, **doc)).status_code == 201
    return application


async def pass_all(api: httpx2.AsyncClient, auth: dict[str, str], *modules: str) -> None:
    for module in modules:
        passed = await complete(api, auth, module, {"version": 1, "answers": answers(module)})
        assert passed.json()["passed"] is True, passed.text


async def roles(db: AsyncSession, user_id: uuid.UUID) -> set[tuple[str, str, str]]:
    rows = await db.execute(select(UserRole).where(UserRole.user_id == user_id))
    return {(r.role, str(r.scope_id), r.status) for r in rows.scalars() if r.role != "customer"}


async def test_approval_with_training_already_passed_verifies_at_once_and_grants_the_hub_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    vikram, vikram_id = await as_user(api, google, VIKRAM)
    application = await ready_for_review(api, priya, "packer", [VOTER_ID, BANK])
    await pass_all(api, priya, "app_basics", "hygiene", "packing")

    approved = await decide(api, vikram, application, outcome="approve")
    mine = await api.get("/v1/partner-applications/me", headers=priya)
    await relay(db_session)

    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert (body["status"], body["next_step"]) == ("verified", None)
    assert body["partner_id"] is not None and body["verified_at"] is not None
    assert {d["review"]["status"] for d in body["documents"]} == {"accepted"}
    assert body["applicant"]["user_id"] == str(priya_id)
    assert mine.json()["partner_id"] == body["partner_id"]
    partner = await db_session.get(Partner, uuid.UUID(body["partner_id"]))
    assert partner is not None and str(partner.application_id) == application
    assert (partner.kind, partner.status, partner.display_name) == (
        "packer",
        "active",
        "Priya Sharma",
    )
    assert [
        e for e in await events(db_session, partners_api.VERIFIED) if e["kind"] != "captain"
    ] == [
        {
            "partner_id": body["partner_id"],
            "user_id": str(priya_id),
            "kind": "packer",
            "hub_id": str(SEED_HUB_ID),
            "requested_society_id": None,
        },
    ]
    assert await events(db_session, partners_api.APPLICATION_DECIDED) == [
        {
            "application_id": application,
            "user_id": str(priya_id),
            "kind": "packer",
            "hub_id": str(SEED_HUB_ID),
            "outcome": "approved",
        }
    ]
    decision = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "partners.application_decided")
        )
    ).scalar_one()
    assert decision.actor_user_id == vikram_id and decision.after is not None
    assert set(decision.after["documents"].values()) == {"accepted"}
    # M1 grants the hub role on partners.verified.
    assert await roles(db_session, priya_id) == {("packer", str(SEED_HUB_ID), "verified")}


async def test_the_last_training_pass_after_approval_verifies_a_captain_without_a_hub_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    ramesh, ramesh_id = await as_user(api, google, RAMESH)
    vikram, _ = await as_user(api, google, VIKRAM)
    application = await ready_for_review(api, ramesh, "captain", CAPTAIN_DOCS)

    approved = await decide(api, vikram, application, outcome="approve")
    first = await complete(
        api, ramesh, "app_basics", {"version": 1, "answers": answers("app_basics")}
    )
    last = await complete(
        api, ramesh, "captain_handover", {"version": 1, "answers": answers("captain_handover")}
    )
    await relay(db_session)

    assert (approved.json()["status"], approved.json()["next_step"]) == ("training", "training")
    assert first.json()["application"]["status"] == "training"
    assert last.json()["application"] == {
        "id": application,
        "status": "verified",
        "next_step": None,
    }
    verified = [
        e for e in await events(db_session, partners_api.VERIFIED) if e["user_id"] == str(ramesh_id)
    ]
    assert len(verified) == 1
    assert (verified[0]["kind"], verified[0]["requested_society_id"]) == ("captain", KRISHNA_KUNJ)
    # Captains get their role from M9's appointment, never from partners.verified.
    assert await roles(db_session, ramesh_id) == set()


async def test_approve_needs_every_document_and_a_kitchen_its_hygiene_grade(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    neha, _ = await as_user(api, google, NEHA)
    stranger = await other_hub_manager(api, google, db_session)
    application = (await apply(api, priya, kind="kitchen")).json()["id"]
    await send(api, priya, application, **VOTER_ID)
    await db_session.commit()

    applied = await decide(api, vikram, application, outcome="approve", hygiene_grade="A")
    await db_session.execute(
        update(Application)
        .where(Application.id == uuid.UUID(application))
        .values(status="kyc_pending")
    )
    await db_session.commit()
    missing = await decide(api, vikram, application, outcome="approve", hygiene_grade="A")
    no_grade = await decide(api, neha, application, outcome="approve")
    other_hub = await decide(api, stranger, application, outcome="reject", reason="x")
    customer = await decide(api, priya, application, outcome="reject", reason="x")

    assert code(applied) == (409, "partners.not_reviewable")
    assert code(missing) == (409, "partners.documents_missing")
    assert missing.json()["doc_types"] == ["pan", "bank", "fssai", "kitchen_photos"]
    assert code(no_grade) == (422, "partners.reason_required")
    assert code(other_hub) == code(customer) == (403, "auth.forbidden")


async def test_resubmit_sends_documents_back_and_a_replacement_returns_to_review(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    application = await ready_for_review(api, priya, "packer", [VOTER_ID, BANK])
    voter = (await api.get(f"/v1/partner-applications/{application}", headers=priya)).json()[
        "documents"
    ][0]
    await db_session.commit()

    no_reason = await decide(
        api, vikram, application, outcome="resubmit", document_ids=[voter["id"]]
    )
    no_documents = await decide(api, vikram, application, outcome="resubmit", reason="Blurred")
    unknown = await decide(
        api,
        vikram,
        application,
        outcome="resubmit",
        reason="Blurred",
        document_ids=[str(uuid.uuid4())],
    )
    sent_back = await decide(
        api,
        vikram,
        application,
        outcome="resubmit",
        reason=" Photo  blurred ",
        document_ids=[voter["id"]],
    )
    mine = await api.get("/v1/partner-applications/me", headers=priya)
    replaced = await send(api, priya, application, **VOTER_ID)
    back = await api.get("/v1/partner-applications/me", headers=priya)
    queue = await api.get(HUB_URL, headers=vikram)

    assert code(no_reason) == code(no_documents) == (422, "partners.reason_required")
    assert code(unknown) == (404, "partners.document_not_found")
    assert sent_back.status_code == 200, sent_back.text
    assert sent_back.json()["status"] == "kyc_pending"
    assert mine.json()["next_step"] == "documents"
    assert mine.json()["requirements"]["documents"][0] == {
        "doc_type": "id_proof",
        "status": "rejected",
        "document_id": voter["id"],
        "reason": "Photo blurred",
    }
    assert replaced.status_code == 201
    assert back.json()["next_step"] == "review"
    assert back.json()["requirements"]["documents"][0]["status"] == "submitted"
    assert [a["id"] for a in queue.json()["items"]] == [application]
    assert [e["outcome"] for e in await events(db_session, partners_api.APPLICATION_DECIDED)] == [
        "resubmit"
    ]


async def test_reject_is_final_and_sets_the_file_retention(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = clients(storage=store_for(s3))
    ramesh, ramesh_id = await as_user(api, google, RAMESH)
    vikram, _ = await as_user(api, google, VIKRAM)
    application = (await apply(api, ramesh, kind="captain")).json()["id"]
    key = await upload_key(db_session, s3, ramesh_id)
    await send(api, ramesh, application, **VOTER_ID, file_keys=[key])
    for doc in CAPTAIN_DOCS[1:]:
        await send(api, ramesh, application, **doc)
    approved = await decide(api, vikram, application, outcome="approve")

    rejected = await decide(api, vikram, application, outcome="reject", reason="Not resident")
    await db_session.commit()
    again = await decide(api, vikram, application, outcome="reject", reason="Again")
    upload = await send(api, ramesh, application, **VOTER_ID)
    withdraw = await post(api, f"/v1/partner-applications/{application}/withdraw", ramesh)
    reapply = await apply(api, ramesh, kind="captain")

    assert approved.json()["status"] == "training"  # reject is allowed in training too
    assert rejected.status_code == 200, rejected.text
    assert (rejected.json()["status"], rejected.json()["reason"]) == ("rejected", "Not resident")
    assert rejected.json()["next_step"] is None and rejected.json()["decided_at"] is not None
    assert code(again) == (409, "partners.not_reviewable")
    assert code(upload) == code(withdraw) == (409, "partners.application_closed")
    assert reapply.status_code == 201
    assert (
        await partners_api.application_reason(db_session, uuid.UUID(application)) == "Not resident"
    )
    assert await partners_api.application_reason(db_session, uuid.uuid4()) is None
    stored = await db_session.get(Upload, key)
    assert stored is not None and stored.delete_after is not None
    assert [e["outcome"] for e in await events(db_session, partners_api.APPLICATION_DECIDED)] == [
        "approved",
        "rejected",
    ]


async def test_hub_roles_follow_suspension_and_reinstatement(db_session: AsyncSession) -> None:
    from gharbasket.modules.identity.service import find_or_create_user

    lata, _ = await find_or_create_user(db_session, LATA)
    kitchen = await service.verify(
        db_session, user_id=lata.id, kind="kitchen", hub_id=SEED_HUB_ID, display_name="Lata tai"
    )
    await relay(db_session)
    granted = await roles(db_session, lata.id)
    await service.suspend(db_session, kitchen, reason_code="hygiene", note="x", by_user_id=None)
    await relay(db_session)
    suspended = await roles(db_session, lata.id)
    await service.reinstate(db_session, kitchen, note=None, by_user_id=None)
    await relay(db_session)

    # The seed already granted Lata kitchen_partner at the seed hub.
    assert granted == {("kitchen_partner", str(SEED_HUB_ID), "verified")}
    assert suspended == {("kitchen_partner", str(SEED_HUB_ID), "suspended")}
    assert await roles(db_session, lata.id) == granted
