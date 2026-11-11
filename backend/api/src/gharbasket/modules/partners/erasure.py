"""M8's part of M1's purge of a deleted account (M1 §3.5 matrix row 5; spec §3.5, "Erasure").

Deleted: her applications, KYC documents (with the encrypted number, hash, mask and bank details)
and training attempts; the KYC files are made due at once, so tonight's `sweep_uploads` deletes
them. Kept without anything personal: her partner records, which M5's payouts and ledger accounts
point at, the kitchen with its skills and grade, and the assignments that back earning entries.
Where she acted as staff, her id is dropped. M1 runs this only once she is owed nothing
(`billing.api.partner_payable_open`), because her last payout needs the bank document.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Executable, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import ratings, service
from gharbasket.modules.partners.models import (
    Application,
    AssignmentEvent,
    Kitchen,
    KycDocument,
    Partner,
    RateCard,
    TrainingAttempt,
)
from gharbasket.platform import files

FORMER = "Former partner"


async def _run(session: AsyncSession, statement: Executable) -> int:
    result = await session.execute(statement)
    return result.rowcount  # pyright: ignore[reportAttributeAccessIssue]


async def erase_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """Erase her M8 data; returns {table: rows} for the tombstone. Running it again changes
    nothing and counts nothing."""
    await service.leave_for_user(session, user_id)  # a hub manager may have reinstated her
    now = datetime.now(UTC)
    counts = {"ratings": await ratings.erase_for_user(session, user_id)}
    keys = await session.scalars(
        select(KycDocument.file_keys).where(KycDocument.user_id == user_id)
    )
    counts["kyc_files"] = sum(
        [await files.set_delete_after(session, key, now) for doc in keys.all() for key in doc]
    )
    counts["kyc_documents"] = await _run(
        session, delete(KycDocument).where(KycDocument.user_id == user_id)
    )
    counts["partners"] = await _run(
        session,
        update(Partner)
        .where(Partner.user_id == user_id, Partner.display_name != FORMER)
        .values(display_name=FORMER, photo_key=None, suspend_note=None, application_id=None),
    )
    for model in (Application, TrainingAttempt):
        counts[model.__tablename__] = await _run(
            session, delete(model).where(model.user_id == user_id)
        )
    personal = (Kitchen.area, Kitchen.address, Kitchen.location, Kitchen.fssai_reg_no)
    counts["kitchens"] = await _run(
        session,
        update(Kitchen)
        .where(
            Kitchen.partner_id.in_(select(Partner.id).where(Partner.user_id == user_id)),
            or_(*(column.is_not(None) for column in personal)),
        )
        .values({column.key: None for column in (*personal, Kitchen.fssai_valid_until)}),
    )
    counts["assignment_events"] = await _run(
        session,
        update(AssignmentEvent).where(AssignmentEvent.user_id == user_id).values(user_id=None),
    )
    # Where she acted as staff (a hub manager whose roles were taken away first).
    for model, column in (
        (Application, Application.created_by),
        (Application, Application.decided_by),
        (KycDocument, KycDocument.reviewed_by),
        (RateCard, RateCard.created_by),
    ):
        await session.execute(update(model).where(column == user_id).values({column.key: None}))
    return counts
