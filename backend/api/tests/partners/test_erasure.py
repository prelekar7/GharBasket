"""M8's part of M1's purge (M1 §3.5 matrix row 5): applications, KYC (rows and files) and training
go; her partner record and kitchen stay for the payouts and ledger with nothing personal; the
purge waits while she is still owed money."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import JournalEntry, Payout, Posting
from gharbasket.modules.identity import jobs as identity_jobs
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners.models import (
    Application,
    Assignment,
    AssignmentEvent,
    Kitchen,
    KycDocument,
    Partner,
    TrainingAttempt,
)
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import Upload

from ..conftest import make_settings
from .payees import make_partner

pytestmark = [pytest.mark.anyio, pytest.mark.services]

# Monday 18 January 2027, 10:00 IST: the run that pays 2027-W02 (as M5's payout tests).
RUN = datetime(2027, 1, 18, 4, 30, tzinfo=UTC)
IN_WEEK = datetime(2027, 1, 13, 6, 0, tzinfo=UTC)


async def a_kitchen_with_history(db: AsyncSession, lata: uuid.UUID) -> Partner:
    """A verified kitchen with KYC files, a training pass, her kitchen and a cooked assignment."""
    partner = await make_partner(db, kind="kitchen", user_id=lata, name="Lata tai")
    partner.photo_key, partner.suspend_note = f"partner_photo/{lata}/a.jpg", "spoke to her"
    keys = [f"kyc_document/{lata}/{n}.jpg" for n in range(2)]
    photos = (
        await db.scalars(
            select(KycDocument).where(
                KycDocument.user_id == lata, KycDocument.doc_type == "kitchen_photos"
            )
        )
    ).one()
    photos.file_keys = keys
    now = datetime.now(UTC)
    held = Assignment(
        partner_id=partner.id,
        hub_id=partner.hub_id,
        on_date=date(2027, 1, 13),
        meal="lunch",
        slot_id=uuid.uuid4(),
        round=1,
        source="fallback",
        status="completed",
        portions=2,
        respond_by=now,
    )
    db.add_all(
        [
            *(
                Upload(
                    key=key,
                    user_id=lata,
                    purpose="kyc_document",
                    content_type="image/jpeg",
                    size_bytes=4,
                    claimed_by="partners",
                    claimed_at=now,
                    delete_after=now + timedelta(days=30),
                )
                for key in keys
            ),
            TrainingAttempt(
                user_id=lata,
                module_code="hygiene",
                module_version=1,
                score=1,
                passed=True,
                answers=[],
            ),
            Kitchen(
                partner_id=partner.id,
                area="Sector 20, Kharghar",
                address="B-302, Palm Beach Residency",
                location=WKTElement("POINT(73.06 19.03)", srid=4326),
                fssai_reg_no="12345678901234",
                fssai_valid_until=date(2028, 1, 1),
                skills=["veg"],
                default_portions={"lunch": [5] * 7, "dinner": [0] * 7},
                hygiene_grade="A",
            ),
            held,
        ]
    )
    await db.flush()
    db.add(AssignmentEvent(assignment_id=held.id, action="ready", user_id=lata))
    await db.flush()
    return partner


async def entry_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(JournalEntry))).scalar_one()


async def ledger_sum(db: AsyncSession) -> int:
    return (await db.execute(select(func.coalesce(func.sum(Posting.amount), 0)))).scalar_one()


async def test_the_purge_waits_until_she_is_paid_then_erases_her_and_keeps_the_books(
    db_session: AsyncSession,
) -> None:
    lata = uuid.uuid4()
    partner = await a_kitchen_with_history(db_session, lata)
    # She once decided someone else's application as a hub manager.
    other = await make_partner(db_session, kind="packer", name="Kavita")
    await db_session.execute(
        update(Application).where(Application.partner_id == other.id).values(decided_by=lata)
    )
    await billing_api.accrue_earning(
        db_session,
        partner_id=partner.id,
        basis="tiffin_portion",
        ref_type="order",
        ref_id=uuid.uuid4(),
        units=2,
        amount_paise=12_000,
        earned_at=IN_WEEK,
    )
    await db_session.commit()

    # Owed money blocks the purge: her last payout needs the bank document the eraser deletes.
    blockers = identity_jobs._blockers  # pyright: ignore[reportPrivateUsage]
    assert await blockers(db_session, lata, None) == ["partner_payable_open"]
    paid = await run_job(db_session, "weekly_payouts", settings=make_settings(), scheduled_for=RUN)
    assert paid.status == "ok", paid.error
    assert await blockers(db_session, lata, None) == []
    entries = await entry_count(db_session)
    books = await ledger_sum(db_session)

    erased = await partners_api.anonymise_for_user(db_session, lata, None)
    again = await partners_api.anonymise_for_user(db_session, lata, None)

    assert erased == {
        "ratings": 0,
        "kyc_files": 2,
        "kyc_documents": 5,
        "partners": 1,
        "applications": 1,
        "training_attempts": 1,
        "kitchens": 1,
        "assignment_events": 1,
    }
    assert set(again.values()) == {0}
    for model in (Application, KycDocument, TrainingAttempt):
        found = await db_session.scalars(select(model).where(model.user_id == lata))
        assert found.all() == []
    uploads = await db_session.scalars(select(Upload).where(Upload.user_id == lata))
    assert all(u.delete_after and u.delete_after <= datetime.now(UTC) for u in uploads.all())

    kept = await db_session.get(Partner, partner.id, populate_existing=True)
    assert kept is not None and kept.status == "left"  # she was active: the eraser makes her leave
    assert (kept.display_name, kept.photo_key, kept.suspend_note, kept.application_id) == (
        "Former partner",
        None,
        None,
        None,
    )
    kitchen = await db_session.get(Kitchen, partner.id, populate_existing=True)
    assert kitchen is not None
    assert (kitchen.area, kitchen.address, kitchen.location, kitchen.fssai_reg_no) == (
        None,
        None,
        None,
        None,
    )
    assert (kitchen.skills, kitchen.hygiene_grade) == (["veg"], "A")
    event = (await db_session.scalars(select(AssignmentEvent))).one()
    await db_session.refresh(event)
    assert event.user_id is None
    decided = (
        await db_session.scalars(select(Application).where(Application.partner_id == other.id))
    ).one()
    await db_session.refresh(decided)
    assert decided.decided_by is None

    # The books are untouched: her earning and payout stand under her partner id.
    assert await ledger_sum(db_session) == books == 0
    assert await entry_count(db_session) == entries
    payouts = await db_session.scalars(select(Payout).where(Payout.partner_id == partner.id))
    (payout,) = payouts.all()
    assert payout.status == "processed" and payout.amount_paise == 12_000
    assert not await billing_api.partner_payable_open(db_session, [partner.id])
