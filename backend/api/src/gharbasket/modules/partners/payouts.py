"""What M5's weekly payouts ask of M8 (spec §3.7, M5 §3.5): may this partner be paid, and to which
bank account.

- **Eligibility.** A partner who is `active`, `paused` or `left` is paid (she still gets what she
  earned after leaving); a `suspended` one is held. Every document her kind needs (§3.1) must be
  current and `accepted`, and her `bank` document must be accepted with a passed penny drop
  (`penny_drop_ok`). The first rule that fails is the reason, in that order: `suspended`,
  `kyc_incomplete` (a document other than the bank one), `no_payout_account` (the bank one).
- **The account.** The only place a KYC number is ever decrypted (§3.1, "Encryption"), and every
  call is audit-logged. Only a real payout provider calls it, never M5's simulated one.

The documents are those of the application the partner was verified from; a seeded partner has
none, so she is held as `kyc_incomplete` until she applies.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import kyc, service
from gharbasket.modules.partners.models import REQUIRED_DOCUMENTS, KycDocument, Partner
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings, get_settings

PAID_STATUSES = ("active", "paused", "left")
HOLD_REASONS = ("kyc_incomplete", "suspended", "no_payout_account")


@dataclass(frozen=True)
class PayoutEligibility:
    eligible: bool
    reason: str | None  # one of HOLD_REASONS when not eligible, else None


@dataclass(frozen=True)
class PayoutAccount:
    account_number: str = field(repr=False)  # never in a log line
    ifsc: str
    holder: str


async def _documents(session: AsyncSession, partner: Partner) -> dict[str, KycDocument]:
    """Her current documents by type, from the application she was verified from."""
    if partner.application_id is None:
        return {}
    found = await kyc.current(session, [partner.application_id])
    return {document.doc_type: document for document in found.get(partner.application_id, [])}


def _bank_ok(document: KycDocument | None) -> bool:
    return (
        document is not None
        and document.review_status == "accepted"
        and document.check_code == "penny_drop_ok"
        and document.number_enc is not None
    )


async def payout_eligibility(session: AsyncSession, partner_id: uuid.UUID) -> PayoutEligibility:
    """May M5 pay this partner now? 404 `partners.partner_not_found` for an unknown partner."""
    partner = await service.get(session, partner_id)
    if partner.status not in PAID_STATUSES:
        return PayoutEligibility(False, "suspended")
    documents = await _documents(session, partner)
    for doc_type in REQUIRED_DOCUMENTS[partner.kind]:
        document = documents.get(doc_type)
        if doc_type != "bank" and (document is None or document.review_status != "accepted"):
            return PayoutEligibility(False, "kyc_incomplete")
    if not _bank_ok(documents.get("bank")):
        return PayoutEligibility(False, "no_payout_account")
    return PayoutEligibility(True, None)


async def payout_account(
    session: AsyncSession,
    partner_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> PayoutAccount:
    """The partner's bank account in clear, for a real payout provider only: decrypted here, and
    every call writes `partners.payout_account_read` to the audit log (who, which document; never
    the number). 409 `partners.no_payout_account` without an accepted bank document whose penny
    drop passed."""
    partner = await service.get(session, partner_id)
    bank = (await _documents(session, partner)).get("bank")
    if bank is None or not _bank_ok(bank):
        raise ProblemError(
            409, "partners.no_payout_account", "This partner has no verified bank account."
        )
    key = (settings or get_settings()).kyc_key
    number = (
        await session.execute(select(func.pgp_sym_decrypt(bank.number_enc, key)))
    ).scalar_one()
    await audit.record(
        session,
        actor_user_id=actor_user_id,
        action="partners.payout_account_read",
        entity_type="partner",
        entity_id=str(partner.id),
        after={"document_id": str(bank.id), "key_version": bank.key_version},
    )
    return PayoutAccount(
        account_number=str(number),
        ifsc=str(bank.details.get("ifsc", "")),
        holder=str(bank.details.get("account_holder", "")),
    )
