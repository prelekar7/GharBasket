"""KYC key and pepper rotation (M8 §3.1): settings, reading and matching across versions, the
`rekey_kyc_documents` job and the `partners.kyc_key_missing` probe."""

import logging
import uuid
from datetime import UTC, datetime

import pytest
import structlog
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import jobs as partner_jobs
from gharbasket.modules.partners import kyc
from gharbasket.modules.partners.models import Application, KycDocument
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, JobRun
from gharbasket.platform.settings import Settings, use_settings

from ..conftest import make_settings
from .payees import BANK_NUMBER, make_partner

KEY1 = "rotation-test-key-one-0123456789abcdef"  # gitleaks:allow
KEY2 = "rotation-test-key-two-0123456789abcdef"  # gitleaks:allow
KEY7 = "rotation-test-key-seven-0123456789abcd"  # gitleaks:allow
PEPPER1 = "rotation-test-pepper-one-0123456789abc"  # gitleaks:allow
PEPPER2 = "rotation-test-pepper-two-0123456789abc"  # gitleaks:allow
V1 = make_settings(kyc_encryption_key=KEY1, kyc_hash_pepper=PEPPER1)
# Rotated: v2 current, v1 retired (step 2 of the runbook).
V2 = make_settings(
    kyc_encryption_key=KEY2,
    kyc_key_version=2,
    kyc_retired_keys=f"1:{KEY1}",
    kyc_hash_pepper=PEPPER2,
    kyc_pepper_version=2,
    kyc_retired_peppers=f"1:{PEPPER1}",
)
# The retired ones removed (step 4).
V2_ONLY = make_settings(
    kyc_encryption_key=KEY2, kyc_key_version=2, kyc_hash_pepper=PEPPER2, kyc_pepper_version=2
)
# Every type with a number, and an ID proof of each kind: (doc_type, id_kind, number).
NUMBERED = [
    ("id_proof", "voter_id", "ABC1234567"),
    ("id_proof", "passport", "K1234567"),
    ("id_proof", "driving_licence", "MH4620230001234"),
    ("pan", None, "ABCDE1234F"),
    ("bank", None, "998877665544"),
    ("fssai", None, "11521999000123"),
    ("driving_licence", None, "MH4620190004321"),
]
SECRETS = [KEY1, KEY2, KEY7, PEPPER1, PEPPER2, BANK_NUMBER, *(n for _, _, n in NUMBERED)]


async def store(
    db: AsyncSession, settings: Settings, doc_type: str, id_kind: str | None, number: str
) -> KycDocument:
    """One document of a new person's open application, stored as `kyc.upload` stores it."""
    user, hub = uuid.uuid4(), uuid.uuid4()
    application = Application(
        user_id=user,
        kind="kitchen",
        scope_type="hub",
        scope_id=hub,
        hub_id=hub,
        display_name="Asha",
        status="applied",
        version=1,
    )
    db.add(application)
    await db.flush()
    document = KycDocument(
        application_id=application.id,
        user_id=user,
        doc_type=doc_type,
        id_kind=id_kind,
        number_hash=kyc.number_hash(settings, id_kind or doc_type, number),
        hash_version=settings.kyc_pepper_version,
        check_status="passed",
        check_code="format_ok",
        checked_at=datetime.now(UTC),
        review_status="pending",
    )
    if doc_type == "fssai":
        document.fssai_reg_no = number
    else:
        document.number_enc = await kyc._encrypt(db, settings, number)
        document.key_version = settings.kyc_key_version
        document.number_masked = kyc.mask(number)
    db.add(document)
    await db.flush()
    return document


async def is_duplicate(
    db: AsyncSession, settings: Settings, doc_type: str, kind: str | None, n: str
) -> bool:
    """Would this number, uploaded by someone else now, be a duplicate?"""
    hashes = kyc.number_hashes(settings, kind or doc_type, n)
    return await kyc._duplicate(db, hashes, uuid.uuid4())


async def decrypt(db: AsyncSession, document: KycDocument, key: str) -> str:
    sql = text("SELECT pgp_sym_decrypt(:enc, :key)")
    return str(await db.scalar(sql, {"enc": document.number_enc, "key": key}))


async def rekey(db: AsyncSession, settings: Settings) -> JobRun:
    use_settings(settings)
    return await run_job(db, "rekey_kyc_documents", settings=settings)


@pytest.mark.anyio
@pytest.mark.services
async def test_a_full_rotation_keeps_reading_and_matching_and_moves_every_row(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    use_settings(V1)
    partner = await make_partner(db_session, name="Meena Patil")
    documents = [await store(db_session, V1, *doc) for doc in NUMBERED]

    # Rotated, before the job: v1 rows still read and match.
    use_settings(V2)
    account = await partners_api.payout_account(db_session, partner.id, settings=V2)
    assert account.account_number == BANK_NUMBER
    for doc_type, kind, number in NUMBERED:
        assert await is_duplicate(db_session, V2, doc_type, kind, number)
        # Only through the retired pepper: under the new one alone nothing matches yet.
        assert not await is_duplicate(db_session, V2_ONLY, doc_type, kind, number)

    with structlog.testing.capture_logs() as logs, caplog.at_level(logging.DEBUG):
        first = await rekey(db_session, V2)
        second = await rekey(db_session, V2)

    encrypted = sum(1 for d, _, _ in NUMBERED if d != "fssai") + 1  # and the partner's bank
    assert first.status == "ok", first.error
    assert first.summary == {
        "rekeyed": encrypted,
        "rehashed": len(NUMBERED),
        "remaining": 0,
        "missing_key": 0,
    }
    assert second.summary == {"rekeyed": 0, "rehashed": 0, "remaining": 0, "missing_key": 0}
    audits = (
        (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "kyc.rekeyed")))
        .scalars()
        .all()
    )
    assert [a.after for a in audits] == [first.summary]  # one row, the counts only
    captured = f"{logs} {caplog.text} {first.summary} {first.error}"
    assert not [secret for secret in SECRETS if secret in captured]

    # After the job, with v1 gone from the settings: still read and matched, now under v2. The
    # job wrote in SQL: the documents this session loaded before it are read again.
    for loaded in list(db_session.identity_map.values()):
        if isinstance(loaded, KycDocument):
            db_session.expire(loaded)
    use_settings(V2_ONLY)
    again = await partners_api.payout_account(db_session, partner.id, settings=V2_ONLY)
    assert again.account_number == BANK_NUMBER
    for document, (doc_type, kind, number) in zip(documents, NUMBERED, strict=True):
        await db_session.refresh(document)
        assert await is_duplicate(db_session, V2_ONLY, doc_type, kind, number)
        # The SQL HMAC is byte for byte Python's, for every type and every ID kind.
        assert document.number_hash == kyc.number_hash(V2_ONLY, kind or doc_type, number)
        assert document.hash_version == 2
        if doc_type == "fssai":
            assert (document.number_enc, document.key_version) == (None, None)
        else:
            assert document.key_version == 2
            assert await decrypt(db_session, document, KEY2) == number
    assert await partner_jobs.kyc_key_missing(db_session, datetime.now(UTC)) == []


@pytest.mark.anyio
@pytest.mark.services
async def test_a_row_under_an_unconfigured_version_is_counted_flagged_and_unreadable(
    db_session: AsyncSession,
) -> None:
    use_settings(V1)
    partner = await make_partner(db_session)
    stale = await store(db_session, V1, "pan", None, "ABCDE1234F")
    # The bank row says key version 7, which no setting names (a key removed too early).
    await db_session.execute(
        update(KycDocument)
        .where(KycDocument.user_id == partner.user_id, KycDocument.doc_type == "bank")
        .values(key_version=7)
    )

    run = await rekey(db_session, V2)

    assert run.summary == {"rekeyed": 1, "rehashed": 1, "remaining": 1, "missing_key": 1}
    await db_session.refresh(stale)
    assert (stale.key_version, stale.hash_version) == (2, 2)
    with pytest.raises(ProblemError) as unavailable:
        await partners_api.payout_account(db_session, partner.id, settings=V2)
    assert (unavailable.value.status, unavailable.value.code) == (
        503,
        "partners.kyc_key_unavailable",
    )
    problems = await partner_jobs.kyc_key_missing(db_session, datetime.now(UTC))
    assert problems == ["1 KYC documents under key version 7, which is not configured"]


@pytest.mark.anyio
@pytest.mark.services
async def test_the_probe_names_a_pepper_version_that_is_gone(db_session: AsyncSession) -> None:
    await store(db_session, V1, "pan", None, "ABCDE1234F")
    await store(db_session, V1, "fssai", None, "11521999000123")
    use_settings(V2_ONLY)  # removed before the job ran

    problems = await partner_jobs.kyc_key_missing(db_session, datetime.now(UTC))

    assert problems == [
        "1 KYC documents under key version 1, which is not configured",
        "2 KYC documents under pepper version 1, which is not configured",
    ]
    assert not [s for s in SECRETS if s in str(problems)]


@pytest.mark.anyio
@pytest.mark.services
async def test_a_failing_statement_shows_no_key_in_the_job_error(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    await store(db_session, V1, "bank", None, "998877665544")
    # Version 1's key configured wrongly: pgcrypto refuses it, and the run fails.
    wrong = make_settings(kyc_encryption_key=KEY2, kyc_key_version=2, kyc_retired_keys=f"1:{KEY7}")

    with structlog.testing.capture_logs() as logs, caplog.at_level(logging.DEBUG):
        run = await rekey(db_session, wrong)

    assert run.status == "failed"
    assert run.error is not None and run.error.startswith("KycStatementError")
    captured = f"{logs} {caplog.text} {run.error}"
    assert not [secret for secret in SECRETS if secret in captured]


def test_a_number_is_hashed_under_every_configured_pepper() -> None:
    hashes = kyc.number_hashes(V2, "pan", "ABCDE1234F")
    assert hashes == {
        1: kyc.number_hash(V1, "pan", "ABCDE1234F"),
        2: kyc.number_hash(V2, "pan", "ABCDE1234F"),  # the current one, which new rows get
    }


# --- settings ---------------------------------------------------------------------------------


def test_retired_keys_and_peppers_are_read_by_version() -> None:
    assert V2.kyc_keys == {1: KEY1, 2: KEY2}
    assert V2.kyc_peppers == {1: PEPPER1.encode(), 2: PEPPER2.encode()}
    assert V1.kyc_keys == {1: KEY1}
    assert make_settings(kyc_retired_keys=" ").kyc_keys == {1: make_settings().kyc_key}


@pytest.mark.parametrize("variable", ["kyc_retired_keys", "kyc_retired_peppers"])
@pytest.mark.parametrize(
    ("value", "message"),
    [
        (f"1:{KEY1}", "lists the current version 1"),
        (f"2:{KEY1},2:{KEY2}", "lists version 2 twice"),
        ("2:short-secret", "(2) must be at least 32 bytes"),
        (f"{KEY1}", "must look like '1:<key>,2:<key>'"),
        (f"two:{KEY1}", "must look like '1:<key>,2:<key>'"),
        (f"2:{KEY1},", "must look like '1:<key>,2:<key>'"),
        ("2:", "must look like '1:<key>,2:<key>'"),
        (f"0:{KEY1}", "a version is 1 to 32767"),
        (f"40000:{KEY1}", "a version is 1 to 32767"),
    ],
)
def test_bad_retired_values_are_refused_naming_the_variable_only(
    variable: str, value: str, message: str
) -> None:
    with pytest.raises(ValidationError) as refused:
        make_settings(**{variable: value})
    assert f"{variable.upper()}" in str(refused.value)
    assert message in str(refused.value)
    for secret in (KEY1, KEY2, "short-secret"):
        assert secret not in str(refused.value)
