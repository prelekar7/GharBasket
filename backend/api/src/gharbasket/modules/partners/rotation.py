"""KYC key and pepper rotation (spec §3.1): the work of the `rekey_kyc_documents` job and of the
`partners.kyc_key_missing` probe.

A row is current when its `key_version` (if it has a number_enc) is KYC_KEY_VERSION and its pepper
version (if it has a number_hash) is KYC_PEPPER_VERSION. The pepper version is `hash_version`, or 1
when that is NULL: the column is added without its CHECK (expand, then contract in a later PR), so
an API still on the old code during a deploy may write hashed rows without it.

The job moves the other rows in batches of 100, each batch one statement: the ids are selected and
locked first, then only those rows are decrypted with the key of their own version (or read from
`fssai_reg_no`, public by law), re-encrypted with the current key and re-hashed with the current
pepper, all inside Postgres. Keys and peppers are bind parameters; the statement returns two flags
per row and never a number, so no number is ever in Python's memory or a log line.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners.kyc import KycStatementError, execute_secret
from gharbasket.modules.partners.models import KycDocument
from gharbasket.platform.settings import Settings

BATCH = 100
# The pepper version of a hashed row (NULL exactly when there is no hash, by the table's CHECK).
row_pepper_version = KycDocument.hash_version

_REKEY = text("""
WITH batch AS MATERIALIZED (
    SELECT d.id
    FROM partners.kyc_documents AS d
    WHERE ((d.number_enc IS NOT NULL AND d.key_version <> CAST(:key_version AS smallint))
           OR (d.number_hash IS NOT NULL
               AND d.hash_version <> CAST(:pepper_version AS smallint)))
      AND (d.number_enc IS NULL OR d.key_version = ANY(CAST(:versions AS smallint[])))
    ORDER BY d.id
    LIMIT :batch
    FOR UPDATE OF d SKIP LOCKED
),
keys AS (
    SELECT k.version, k.key
    FROM unnest(CAST(:versions AS smallint[]), CAST(:keys AS text[])) AS k(version, key)
),
plain AS MATERIALIZED (
    SELECT d.id, d.key_version AS old_key_version,
           d.hash_version AS old_hash_version,
           COALESCE(d.fssai_reg_no, pgp_sym_decrypt(d.number_enc, k.key)) AS number
    FROM batch AS b
    JOIN partners.kyc_documents AS d ON d.id = b.id
    LEFT JOIN keys AS k ON k.version = d.key_version
)
UPDATE partners.kyc_documents AS d
SET number_enc = CASE
        WHEN d.key_version <> CAST(:key_version AS smallint)
        THEN pgp_sym_encrypt(p.number, CAST(:new_key AS text))
        ELSE d.number_enc
    END,
    key_version = CASE WHEN d.number_enc IS NULL THEN NULL ELSE CAST(:key_version AS smallint) END,
    number_hash = CASE
        WHEN d.number_hash IS NULL THEN NULL
        ELSE hmac(COALESCE(d.id_kind, d.doc_type) || ':' || p.number,
                  CAST(:new_pepper AS text), 'sha256')
    END,
    hash_version = CASE
        WHEN d.number_hash IS NULL THEN NULL ELSE CAST(:pepper_version AS smallint)
    END
FROM plain AS p
WHERE d.id = p.id
RETURNING p.old_key_version IS DISTINCT FROM d.key_version AS rekeyed,
          p.old_hash_version IS DISTINCT FROM d.hash_version AS rehashed
""")

# Does one row's number decrypt under `key`, and does its hash match under `pepper`? Computed in
# Postgres; only the two booleans come back.
_CHECK = text("""
SELECT (d.fssai_reg_no IS NOT NULL OR pgp_sym_decrypt(d.number_enc, :key) IS NOT NULL) AS reads,
       hmac(COALESCE(d.id_kind, d.doc_type) || ':'
                || COALESCE(d.fssai_reg_no, pgp_sym_decrypt(d.number_enc, :key)),
            CAST(:pepper AS text), 'sha256') = d.number_hash AS matches
FROM partners.kyc_documents AS d
WHERE d.id = :id
""")


@dataclass(frozen=True)
class Rekeyed:
    rekeyed: int
    rehashed: int


async def rekey_batch(
    session: AsyncSession, settings: Settings, limit: int | None = None
) -> Rekeyed | None:
    """Move up to BATCH rows that are not current to the current key and pepper; None when none
    could be moved (all current, missing a key, or locked by another run). A configured key that
    is wrong fails the batch that reaches its row (KycStatementError, SQLSTATE 39000)."""
    keys = settings.kyc_keys
    result = await execute_secret(
        session,
        _REKEY,
        {
            "versions": list(keys),
            "keys": list(keys.values()),
            "key_version": settings.kyc_key_version,
            "pepper_version": settings.kyc_pepper_version,
            "new_key": settings.kyc_key,
            "new_pepper": settings.kyc_pepper.decode(),
            "batch": limit or BATCH,
        },
    )
    rows = result.all()
    if not rows:
        return None
    return Rekeyed(sum(1 for r in rows if r.rekeyed), sum(1 for r in rows if r.rehashed))


def _stale(settings: Settings) -> ColumnElement[bool]:
    return or_(
        and_(
            KycDocument.number_enc.is_not(None),
            KycDocument.key_version != settings.kyc_key_version,
        ),
        row_pepper_version != settings.kyc_pepper_version,
    )


async def remaining(session: AsyncSession, settings: Settings) -> tuple[int, int]:
    """(rows not current, those of them under a key version with no configured key)."""
    missing = KycDocument.number_enc.is_not(None) & KycDocument.key_version.not_in(
        list(settings.kyc_keys)
    )
    row = (
        await session.execute(
            select(func.count(), func.count().filter(missing)).where(_stale(settings))
        )
    ).one()
    return int(row[0]), int(row[1])


def _documents(n: int) -> str:
    return "1 KYC document" if n == 1 else f"{n} KYC documents"


async def unconfigured(session: AsyncSession, settings: Settings) -> list[str]:
    """The stored key and pepper versions with no configured key or pepper, with row counts: those
    numbers cannot be read (key) or matched as duplicates (pepper) until it is configured again."""
    lines: list[str] = []
    for column, configured, what in (
        (KycDocument.key_version, settings.kyc_keys, "key"),
        (row_pepper_version, settings.kyc_peppers, "pepper"),
    ):
        rows = await session.execute(
            select(column, func.count())
            .where(column.is_not(None), column.not_in(list(configured)))
            .group_by(column)
            .order_by(column)
        )
        lines += [
            f"{_documents(n)} under {what} version {version}, which is not configured"
            for version, n in rows.all()
        ]
    return lines


async def _check(
    session: AsyncSession, document_id: object, key: str, pepper: bytes
) -> tuple[bool, bool | None] | None:
    """(the number decrypts, its hash matches) for one row; None when the key fails. Inside a
    savepoint, so a refused key leaves the probe's transaction usable."""
    try:
        async with session.begin_nested():
            row = (
                await execute_secret(
                    session, _CHECK, {"id": document_id, "key": key, "pepper": pepper.decode()}
                )
            ).one()
    except KycStatementError:
        return None
    return bool(row.reads), row.matches


async def wrong_secrets(session: AsyncSession, settings: Settings) -> list[str]:
    """A configured key that does not decrypt, or pepper that does not reproduce the stored hash,
    tested on one row per stored version. Versions only, never a value."""
    keys, peppers = settings.kyc_keys, settings.kyc_peppers
    lines: list[str] = []
    for version, key in sorted(keys.items()):
        sample = await session.scalar(
            select(KycDocument.id)
            .where(KycDocument.number_enc.is_not(None), KycDocument.key_version == version)
            .limit(1)
        )
        if sample is not None and await _check(session, sample, key, settings.kyc_pepper) is None:
            lines.append(f"KYC key version {version} does not decrypt")
    for version, pepper in sorted(peppers.items()):
        # A row whose number can be read: in clear (FSSAI), or under a configured key.
        readable = KycDocument.fssai_reg_no.is_not(None) | KycDocument.key_version.in_(list(keys))
        found = (
            await session.execute(
                select(KycDocument.id, KycDocument.key_version)
                .where(row_pepper_version == version, readable)
                .order_by(KycDocument.fssai_reg_no.is_(None))  # FSSAI first: no key involved
                .limit(1)
            )
        ).first()
        if found is None:
            continue
        key = keys.get(found.key_version or 0, settings.kyc_key)
        checked = await _check(session, found.id, key, pepper)
        if checked is not None and checked[1] is False:
            lines.append(f"KYC pepper version {version} does not match")
    return lines
