"""Partners as M5's payouts see them, written straight to the tables: a verified partner of a kind
with the documents of her application in a chosen state (M8 §3.1, §3.7).

The documents and the partner are made directly, not through the endpoints, so a payout test can
put any one of them in the state it needs (a pending ID, a failed penny drop, a suspension)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners.models import (
    REQUIRED_DOCUMENTS,
    Application,
    KycDocument,
    Partner,
)
from gharbasket.platform.settings import get_settings

BANK_NUMBER = "123456789012"
IFSC = "SBIN0001234"


async def make_partner(
    db: AsyncSession,
    *,
    kind: str = "packer",
    status: str = "active",
    hub_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    name: str = "Kavita",
    review: str = "accepted",
    bank_review: str | None = None,
    bank_number: str = BANK_NUMBER,
    missing: tuple[str, ...] = (),
    application: bool = True,
) -> Partner:
    """A partner verified from an application whose required documents are current with the
    review state `review` (the bank document `bank_review`, default the same); `missing` types
    are left out. A bank number ending in 0000 fails the simulated penny drop (M8 §3.1)."""
    now = datetime.now(UTC)
    hub = hub_id or uuid.uuid4()
    person = user_id or uuid.uuid4()
    partner = Partner(
        user_id=person,
        kind=kind,
        hub_id=hub,
        display_name=name,
        status=status,
        verified_at=now,
        version=1,
    )
    if status == "suspended":
        partner.suspended_at, partner.suspend_reason_code = now, "conduct"
    if status == "left":
        partner.suspend_reason_code = "left"
    db.add(partner)
    await db.flush()
    if not application:
        return partner  # a seeded partner: no application, so no documents
    applied = Application(
        user_id=person,
        kind=kind,
        scope_type="hub",
        scope_id=hub,
        hub_id=hub,
        display_name=name,
        status="verified",
        partner_id=partner.id,
        verified_at=now,
        version=1,
    )
    db.add(applied)
    await db.flush()
    partner.application_id = applied.id
    for doc_type in REQUIRED_DOCUMENTS[kind]:
        if doc_type in missing:
            continue
        document = KycDocument(
            application_id=applied.id,
            user_id=person,
            doc_type=doc_type,
            check_status="passed",
            check_code="format_ok",
            checked_at=now,
            review_status=review,
            created_at=now,
        )
        if doc_type == "id_proof":
            document.id_kind = "voter_id"
        if doc_type == "bank":
            settings = get_settings()
            document.number_enc = (
                await db.execute(select(func.pgp_sym_encrypt(bank_number, settings.kyc_key)))
            ).scalar_one()
            document.key_version = settings.kyc_key_version
            document.number_masked = "X" * (len(bank_number) - 4) + bank_number[-4:]
            document.details = {"ifsc": IFSC, "account_holder": name}
            failed = bank_number.endswith("0000")
            document.check_status = "failed" if failed else "passed"
            document.check_code = "penny_drop_failed" if failed else "penny_drop_ok"
            document.review_status = bank_review or review
        db.add(document)
    await db.flush()
    return partner
