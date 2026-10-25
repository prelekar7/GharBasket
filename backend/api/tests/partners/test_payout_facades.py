"""M8's payout facades for M5 (M8 §3.7, M5 §3.5 step 3): who may be paid, and the bank account a
real provider would pay, decrypted only there and audit-logged on every read."""

import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners.models import KycDocument
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import AuditEntry

from .payees import BANK_NUMBER, IFSC, make_partner

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def reason(db: AsyncSession, partner_id: uuid.UUID) -> tuple[bool, str | None]:
    found = await partners_api.payout_eligibility(db, partner_id)
    return found.eligible, found.reason


@pytest.mark.parametrize("status", ["active", "paused", "left"])
async def test_active_paused_and_left_partners_with_accepted_documents_are_paid(
    db_session: AsyncSession, status: str
) -> None:
    for kind in ("kitchen", "captain", "rider", "packer"):
        partner = await make_partner(db_session, kind=kind, status=status)
        assert await reason(db_session, partner.id) == (True, None), kind


async def test_each_rule_holds_with_its_reason_in_order(db_session: AsyncSession) -> None:
    # Suspended wins over everything, even with no documents at all.
    suspended = await make_partner(db_session, status="suspended", review="pending")
    assert await reason(db_session, suspended.id) == (False, "suspended")
    # A required document other than the bank one missing, pending or rejected.
    for partner in (
        await make_partner(db_session, kind="kitchen", missing=("fssai",)),
        await make_partner(db_session, review="pending", bank_review="accepted"),
        await make_partner(db_session, review="rejected", bank_review="accepted"),
        await make_partner(db_session, application=False),  # seeded: no application
    ):
        assert await reason(db_session, partner.id) == (False, "kyc_incomplete")
    # Everything else accepted, but the bank document missing, not accepted, or its penny drop
    # failed (an account ending in 0000, M8 §3.1's test hook).
    for partner in (
        await make_partner(db_session, missing=("bank",)),
        await make_partner(db_session, bank_review="pending"),
        await make_partner(db_session, bank_review="rejected"),
        await make_partner(db_session, bank_number="123456780000"),
    ):
        assert await reason(db_session, partner.id) == (False, "no_payout_account")


async def test_a_duplicate_bank_number_is_no_payout_account(db_session: AsyncSession) -> None:
    partner = await make_partner(db_session)
    await db_session.execute(
        update(KycDocument)
        .where(KycDocument.user_id == partner.user_id, KycDocument.doc_type == "bank")
        .values(check_status="failed", check_code="duplicate")
    )
    assert await reason(db_session, partner.id) == (False, "no_payout_account")


async def test_a_superseded_document_does_not_count(db_session: AsyncSession) -> None:
    partner = await make_partner(db_session)
    bank = (
        await db_session.execute(
            select(KycDocument).where(
                KycDocument.user_id == partner.user_id, KycDocument.doc_type == "bank"
            )
        )
    ).scalar_one()
    newer = KycDocument(
        application_id=bank.application_id,
        user_id=bank.user_id,
        doc_type="bank",
        check_status="passed",
        check_code="penny_drop_ok",
        checked_at=bank.checked_at,
        review_status="pending",
        created_at=bank.created_at,
    )
    db_session.add(newer)
    await db_session.flush()
    bank.superseded_by = newer.id
    await db_session.flush()
    # The replacement is still pending review: no payout account until it is accepted.
    assert await reason(db_session, partner.id) == (False, "no_payout_account")


async def test_an_unknown_partner_is_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(ProblemError) as missing:
        await partners_api.payout_eligibility(db_session, uuid.uuid4())
    assert missing.value.code == "partners.partner_not_found"


async def test_the_payout_account_is_decrypted_and_every_read_is_audited(
    db_session: AsyncSession,
) -> None:
    partner = await make_partner(db_session, name="Meena Patil")
    admin = uuid.uuid4()

    account = await partners_api.payout_account(db_session, partner.id, actor_user_id=admin)
    again = await partners_api.payout_account(db_session, partner.id)

    assert (account.account_number, account.ifsc, account.holder) == (
        BANK_NUMBER,
        IFSC,
        "Meena Patil",
    )
    assert again == account
    assert BANK_NUMBER not in repr(account)  # never in a log line
    reads = list(
        (
            await db_session.execute(
                select(AuditEntry)
                .where(
                    AuditEntry.action == "partners.payout_account_read",
                    AuditEntry.entity_id == str(partner.id),
                )
                .order_by(AuditEntry.id)
            )
        ).scalars()
    )
    assert [r.actor_user_id for r in reads] == [admin, None]
    assert all(BANK_NUMBER not in str(r.after) for r in reads)


async def test_no_payout_account_without_an_accepted_bank_with_a_passed_penny_drop(
    db_session: AsyncSession,
) -> None:
    for partner in (
        await make_partner(db_session, bank_number="123456780000"),
        await make_partner(db_session, bank_review="pending"),
        await make_partner(db_session, application=False),
    ):
        with pytest.raises(ProblemError) as refused:
            await partners_api.payout_account(db_session, partner.id)
        assert refused.value.code == "partners.no_payout_account"
    reads = await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "partners.payout_account_read")
    )
    assert reads.first() is None  # nothing decrypted, nothing to log


async def test_partners_are_read_in_batches_and_per_person(db_session: AsyncSession) -> None:
    person = uuid.uuid4()
    rider = await make_partner(db_session, kind="rider", user_id=person, name="Arjun")
    packer = await make_partner(db_session, kind="packer", user_id=person, name="Arjun")
    other = await make_partner(db_session)

    found = await partners_api.get_partners(db_session, [rider.id, other.id, uuid.uuid4()])
    mine = await partners_api.partners_for_user(db_session, person)

    assert set(found) == {rider.id, other.id} and found[rider.id].kind == "rider"
    assert {p.id for p in mine} == {rider.id, packer.id}
    assert await partners_api.get_partners(db_session, []) == {}
