"""The hub's decision on an application and verification (spec §3.1).

`approve` (from `kyc_pending`, every required document current) accepts the documents and moves
the application to `training`, or verifies it at once when the training is already passed.
`resubmit` sends listed documents back; `reject` ends it (also allowed in `training`). Whichever
comes last, the approval or the last required training pass, verifies the person in the same
transaction: the partner record, `partner_id`, `verified_at` and `partners.verified`.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import events, kyc, service, training
from gharbasket.modules.partners.models import REQUIRED_DOCUMENTS, REQUIRED_TRAININGS, Application
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

# The outcome as the event names it (§5).
OUTCOMES = {"approve": "approved", "resubmit": "resubmit", "reject": "rejected"}


def _reason_required(detail: str) -> ProblemError:
    return ProblemError(422, "partners.reason_required", detail)


async def verify_if_complete(session: AsyncSession, application: Application) -> bool:
    """Verify an approved application once every required module is passed (§3.1)."""
    if application.status != "training":
        return False
    passed = (await training.states_for_users(session, [application.user_id])).get(
        application.user_id, {}
    )
    if any(
        code not in passed or passed[code].status != "passed"
        for code in REQUIRED_TRAININGS[application.kind]
    ):
        return False
    partner = await service.verify(
        session,
        user_id=application.user_id,
        kind=application.kind,
        hub_id=application.hub_id,
        display_name=application.display_name,
        application_id=application.id,
        requested_society_id=application.scope_id if application.scope_type == "society" else None,
        data_source=application.data_source,
    )
    application.status = "verified"
    application.partner_id = partner.id
    application.verified_at = partner.verified_at
    application.version += 1
    await session.flush()
    return True


async def decide(
    session: AsyncSession,
    application: Application,
    *,
    outcome: str,
    reason: str | None,
    document_ids: list[uuid.UUID],
    hygiene_grade: str | None,
    by_user_id: uuid.UUID,
) -> Application:
    """Approve, ask for documents again, or reject; audit-logged with the document review states,
    and `partners.application_decided` published."""
    if outcome in ("resubmit", "reject") and not reason:
        raise _reason_required("Give the applicant a reason.")
    if outcome == "resubmit" and not document_ids:
        raise _reason_required("Choose the documents to redo.")
    if outcome == "approve" and application.kind == "kitchen" and hygiene_grade is None:
        raise _reason_required("Grade the kitchen's hygiene from its photos.")
    if application.status != "kyc_pending" and not (
        outcome == "reject" and application.status == "training"
    ):
        raise ProblemError(
            409, "partners.not_reviewable", "This application is not waiting for review."
        )
    documents = await kyc.for_application(session, application)
    now = datetime.now(UTC)
    before = {"status": application.status}
    if outcome == "approve":
        missing = [
            doc_type
            for doc_type in REQUIRED_DOCUMENTS[application.kind]
            if doc_type not in {document.doc_type for document in documents}
        ]
        if missing:
            raise ProblemError(
                409,
                "partners.documents_missing",
                "Some required documents are missing.",
                extra={"doc_types": missing},
            )
        for document in documents:
            document.review_status, document.review_reason = "accepted", None
            document.reviewed_by, document.reviewed_at = by_user_id, now
        application.status, application.docs_approved_at = "training", now
        application.hygiene_grade = hygiene_grade if application.kind == "kitchen" else None
    elif outcome == "resubmit":
        by_id = {document.id: document for document in documents}
        if any(document_id not in by_id for document_id in document_ids):
            raise kyc.not_found()
        for document_id in document_ids:
            document = by_id[document_id]
            document.review_status, document.review_reason = "rejected", reason
            document.reviewed_by, document.reviewed_at = by_user_id, now
    else:
        application.status, application.reason = "rejected", reason
        await kyc.release_files(session, documents, now)
    application.decided_by, application.decided_at = by_user_id, now
    application.version += 1
    await session.flush()
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="partners.application_decided",
        entity_type="partner_application",
        entity_id=str(application.id),
        before=before,
        after={
            "outcome": OUTCOMES[outcome],
            "status": application.status,
            "reason": reason,
            "hygiene_grade": application.hygiene_grade,
            "documents": {str(d.id): d.review_status for d in documents},
        },
    )
    await publish(
        session,
        events.APPLICATION_DECIDED,
        {
            "application_id": str(application.id),
            "user_id": str(application.user_id),
            "kind": application.kind,
            "hub_id": str(application.hub_id),
            "outcome": OUTCOMES[outcome],
        },
    )
    if outcome == "approve":
        await verify_if_complete(session, application)
    return application
