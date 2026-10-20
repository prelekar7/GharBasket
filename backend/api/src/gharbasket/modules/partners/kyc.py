"""KYC documents (spec §3.1; handbook 13 §13.4, §13.5, §13.8).

No Aadhaar, ever: refused before anything is stored. Checks are simulated (no verification API in
the prototype): a format check, a ₹1 penny drop for bank accounts (an account ending in 0000
fails, the test hook in every environment), and a duplicate check across people by an HMAC of the
number. A failed check does not block the upload: the hub manager sees it and decides.

Numbers are stored only as `pgp_sym_encrypt(number, KYC_ENCRYPTION_KEY)` with its key version, a
masked copy (the last 4 characters) and the HMAC; nothing here decrypts. The FSSAI registration
number is public by law and kept in clear. Files are the applicant's own private `kyc_document`
uploads, claimed here and read only through 5-minute links for hub staff (audit-logged).
"""

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners.models import (
    OPEN_STATUSES,
    REQUIRED_DOCUMENTS,
    Application,
    KycDocument,
)
from gharbasket.platform import files
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
PURPOSE = "kyc_document"
RETENTION = timedelta(days=30)  # after rejection, withdrawal, superseding or leaving (M0 §3.1)
ID_KINDS = ("voter_id", "passport", "driving_licence")
# Types that carry a number; kitchen photos carry none.
NUMBERED = ("id_proof", "pan", "bank", "fssai", "driving_licence")
FORMATS = {
    "pan": re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]"),
    "voter_id": re.compile(r"[A-Z]{3}[0-9]{7}"),
    "passport": re.compile(r"[A-Z][0-9]{7}"),
    "driving_licence": re.compile(r"[A-Z0-9]{15,16}"),
    "fssai": re.compile(r"[0-9]{14}"),
    "bank": re.compile(r"[0-9]{9,18}"),
}
IFSC = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")
# State, district, series, number (MH46AB1234), or the Bharat series (22BH1234AB).
VEHICLE = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}|[0-9]{2}BH[0-9]{4}[A-Z]{1,2}")
AADHAAR = re.compile(r"[0-9]{12}")
FILE_COUNTS = {"kitchen_photos": (2, 4)}
DEFAULT_FILE_COUNT = (0, 2)


def not_found() -> ProblemError:
    return ProblemError(404, "partners.document_not_found", "No such document.")


def _invalid(field: str, detail: str) -> ProblemError:
    return ProblemError(422, "partners.number_invalid", detail, extra={"field": field})


def _aadhaar() -> ProblemError:
    return ProblemError(
        422, "partners.aadhaar_not_accepted", "Aadhaar is not accepted; use another ID."
    )


def _doc_type_invalid(detail: str) -> ProblemError:
    return ProblemError(422, "partners.doc_type_invalid", detail)


def normalise(value: str) -> str:
    """Upper case, without spaces and hyphens."""
    return re.sub(r"[\s-]", "", value).upper()


def mask(number: str) -> str:
    """Only the last 4 characters show."""
    return "X" * max(len(number) - 4, 0) + number[-4:]


@dataclass(frozen=True)
class Clean:
    doc_type: str
    id_kind: str | None
    number: str | None
    details: dict[str, Any]


def validate(kind: str, body: dict[str, Any], *, today: date) -> Clean:
    """The document's type, number and details, normalised; 422 for anything the kind does not
    need, any Aadhaar, a bad format or an expired document. Nothing is stored before this."""
    doc_type = body["doc_type"]
    if doc_type not in REQUIRED_DOCUMENTS[kind]:
        raise _doc_type_invalid(f"A {kind} application does not need this document.")
    id_kind = None
    if doc_type == "id_proof":
        id_kind = (body.get("id_kind") or "").strip().lower()
        if id_kind == "aadhaar":
            raise _aadhaar()
        if id_kind not in ID_KINDS:
            raise _doc_type_invalid("Choose voter ID, passport or driving licence.")
    number = None
    if doc_type in NUMBERED:
        number = normalise(body.get("number") or "")
        if doc_type in ("id_proof", "driving_licence") and AADHAAR.fullmatch(number):
            raise _aadhaar()
        if not FORMATS[id_kind or doc_type].fullmatch(number):
            raise _invalid("number", "This number is not in the right format.")
    elif body.get("number"):
        raise _invalid("number", "Photos carry no number.")
    details: dict[str, Any] = {}
    if doc_type == "bank":
        ifsc = normalise(body.get("ifsc") or "")
        if not IFSC.fullmatch(ifsc):
            raise _invalid("ifsc", "The IFSC is not in the right format.")
        holder = " ".join((body.get("account_holder") or "").split())
        if not 2 <= len(holder) <= 80:
            raise _invalid("account_holder", "Give the account holder's name.")
        details = {"ifsc": ifsc, "account_holder": holder}
    if doc_type in ("fssai", "driving_licence"):
        valid_until: date | None = body.get("valid_until")
        if valid_until is None:
            raise _invalid("valid_until", "Give the date it is valid until.")
        if valid_until <= today:
            raise ProblemError(422, "partners.document_expired", "This document has expired.")
        details["valid_until"] = valid_until.isoformat()
    if doc_type == "driving_licence":
        vehicle = normalise(body.get("vehicle_no") or "")
        if not VEHICLE.fullmatch(vehicle):
            raise _invalid("vehicle_no", "The vehicle number is not in the right format.")
        details["vehicle_no"] = vehicle
    low, high = FILE_COUNTS.get(doc_type, DEFAULT_FILE_COUNT)
    if not low <= len(set(body["file_keys"])) <= high or len(set(body["file_keys"])) != len(
        body["file_keys"]
    ):
        raise _file_key_invalid(f"Send {low} to {high} different files.")
    return Clean(doc_type, id_kind, number, details)


def _file_key_invalid(detail: str = "This file is not one of your KYC uploads.") -> ProblemError:
    return ProblemError(422, "partners.file_key_invalid", detail)


async def _claim(
    session: AsyncSession, keys: list[str], user_id: uuid.UUID, store: files.ObjectStore | None
) -> None:
    """Each key is the caller's own kyc_document upload with the object behind it (M0 §3.1)."""
    if not keys:
        return
    if store is None:
        raise files.storage_unavailable()
    for key in keys:
        upload = await files.verify(session, key, user_id=user_id, purpose=PURPOSE)
        stored = await store.head(key) if upload is not None else None
        if (
            upload is None
            or stored is None
            or (stored.content_type, stored.size_bytes) != (upload.content_type, upload.size_bytes)
        ):
            raise _file_key_invalid()
    for key in keys:
        if await files.claim(session, key, user_id=user_id, purpose=PURPOSE, by="partners") is None:
            raise _file_key_invalid()


def number_hash(settings: Settings, kind: str, number: str) -> bytes:
    """HMAC-SHA256 with KYC_HASH_PEPPER, for duplicate checks; never reversible."""
    return hmac.new(settings.kyc_pepper, f"{kind}:{number}".encode(), hashlib.sha256).digest()


async def _encrypt(session: AsyncSession, settings: Settings, number: str) -> bytes:
    """pgp_sym_encrypt in Postgres (pgcrypto), the key sent as a bind parameter. A statement of
    its own, so no INSERT error could ever echo the key."""
    return (
        await session.execute(select(func.pgp_sym_encrypt(number, settings.kyc_key)))
    ).scalar_one()


async def _duplicate(session: AsyncSession, digest: bytes, user_id: uuid.UUID) -> bool:
    """The same number on another person's open application or partner record."""
    found = await session.execute(
        select(KycDocument.id)
        .join(Application, Application.id == KycDocument.application_id)
        .where(
            KycDocument.number_hash == digest,
            KycDocument.user_id != user_id,
            KycDocument.superseded_by.is_(None),
            Application.status.in_((*OPEN_STATUSES, "verified")),
        )
        .limit(1)
    )
    return found.first() is not None


def _check(doc_type: str, number: str | None, duplicate: bool) -> tuple[str, str]:
    if duplicate:
        return "failed", "duplicate"
    if doc_type == "bank":
        # The simulated ₹1 penny drop: an account ending in 0000 fails (the test hook).
        assert number is not None
        return (
            ("failed", "penny_drop_failed")
            if number.endswith("0000")
            else (
                "passed",
                "penny_drop_ok",
            )
        )
    return "passed", "format_ok"


async def current(
    session: AsyncSession, application_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[KycDocument]]:
    """Each application's current documents (not superseded), oldest first."""
    if not application_ids:
        return {}
    rows = await session.execute(
        select(KycDocument)
        .where(
            KycDocument.application_id.in_(set(application_ids)),
            KycDocument.superseded_by.is_(None),
        )
        .order_by(KycDocument.created_at, KycDocument.id)
    )
    out: dict[uuid.UUID, list[KycDocument]] = {}
    for document in rows.scalars():
        out.setdefault(document.application_id, []).append(document)
    return out


async def upload(
    session: AsyncSession,
    application: Application,
    body: dict[str, Any],
    *,
    settings: Settings,
    store: files.ObjectStore | None,
) -> KycDocument:
    """Store one document of the caller's open application; a second of the same type replaces
    the first. The application moves to `kyc_pending` when every required type is there."""
    now = datetime.now(UTC)
    clean = validate(application.kind, body, today=now.astimezone(IST).date())
    keys = list(body["file_keys"])
    await _claim(session, keys, application.user_id, store)
    digest = None
    if clean.number is not None:
        digest = number_hash(settings, clean.id_kind or clean.doc_type, clean.number)
    duplicate = digest is not None and await _duplicate(session, digest, application.user_id)
    check_status, check_code = _check(clean.doc_type, clean.number, duplicate)
    document = KycDocument(
        application_id=application.id,
        user_id=application.user_id,
        doc_type=clean.doc_type,
        id_kind=clean.id_kind,
        number_hash=digest,
        details=clean.details,
        file_keys=keys,
        check_status=check_status,
        check_code=check_code,
        checked_at=now,
        review_status="pending",
        created_at=now,
    )
    if clean.doc_type == "fssai":
        document.fssai_reg_no = clean.number  # public by law (§3.1)
    elif clean.number is not None:
        document.number_enc = await _encrypt(session, settings, clean.number)
        document.key_version = settings.kyc_key_version
        document.number_masked = mask(clean.number)
    session.add(document)
    await session.flush()
    for old in (await current(session, [application.id])).get(application.id, []):
        if old.doc_type == clean.doc_type and old.id != document.id:
            old.superseded_by = document.id
            await release_files(session, [old], now)
    present = {d.doc_type for d in (await current(session, [application.id]))[application.id]}
    if application.status == "applied" and set(REQUIRED_DOCUMENTS[application.kind]) <= present:
        application.status = "kyc_pending"
    application.version += 1
    await session.flush()
    return document


async def release_files(session: AsyncSession, documents: list[KycDocument], at: datetime) -> None:
    """The documents' files may be deleted 30 days from `at` (M0 §3.1)."""
    for document in documents:
        for key in document.file_keys:
            await files.set_delete_after(session, key, at + RETENTION)
    await session.flush()


async def for_application(session: AsyncSession, application: Application) -> list[KycDocument]:
    return (await current(session, [application.id])).get(application.id, [])


async def all_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[KycDocument]:
    rows = await session.execute(
        select(KycDocument).where(KycDocument.user_id == user_id).order_by(KycDocument.created_at)
    )
    return list(rows.scalars())
