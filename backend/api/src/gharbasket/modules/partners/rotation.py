"""KYC key and pepper rotation (spec §3.1): the work of the `rekey_kyc_documents` job and of the
`partners.kyc_key_missing` probe.

A row is current when its `key_version` (if it has a number_enc) is KYC_KEY_VERSION and its
`hash_version` (if it has a number_hash) is KYC_PEPPER_VERSION. The job moves the others in batches
of 100, each batch one statement: the number is decrypted with the key of the row's own version (or
read from `fssai_reg_no`, public by law), re-encrypted with the current key and re-hashed with the
current pepper, all inside Postgres. Keys and peppers are bind parameters; the statement returns
two flags per row and never a number, so no number is ever in Python's memory or a log line.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners.kyc import execute_secret
from gharbasket.modules.partners.models import KycDocument
from gharbasket.platform.settings import Settings

BATCH = 100

# `keys` maps every configured key version to its key. A row whose key version is not there is
# left alone (the LEFT JOIN finds no key) and counted as `missing_key` afterwards.
_REKEY = text("""
WITH keys AS (
    SELECT k.version, k.key
    FROM unnest(CAST(:versions AS smallint[]), CAST(:keys AS text[])) AS k(version, key)
),
batch AS MATERIALIZED (
    SELECT d.id, d.key_version AS old_key_version, d.hash_version AS old_hash_version,
           COALESCE(d.fssai_reg_no, pgp_sym_decrypt(d.number_enc, k.key)) AS number
    FROM partners.kyc_documents AS d
    LEFT JOIN keys AS k ON k.version = d.key_version
    WHERE (d.key_version <> CAST(:key_version AS smallint)
           OR d.hash_version <> CAST(:pepper_version AS smallint))
      AND (d.number_enc IS NULL OR k.key IS NOT NULL)
    ORDER BY d.id
    LIMIT :batch
    FOR UPDATE OF d SKIP LOCKED
)
UPDATE partners.kyc_documents AS d
SET number_enc = CASE
        WHEN d.key_version <> CAST(:key_version AS smallint)
        THEN pgp_sym_encrypt(b.number, CAST(:new_key AS text))
        ELSE d.number_enc
    END,
    key_version = CASE WHEN d.number_enc IS NULL THEN NULL ELSE CAST(:key_version AS smallint) END,
    number_hash = CASE
        WHEN d.number_hash IS NULL THEN NULL
        ELSE hmac(COALESCE(d.id_kind, d.doc_type) || ':' || b.number,
                  CAST(:new_pepper AS text), 'sha256')
    END,
    hash_version = CASE
        WHEN d.number_hash IS NULL THEN NULL ELSE CAST(:pepper_version AS smallint)
    END
FROM batch AS b
WHERE d.id = b.id
RETURNING b.old_key_version IS DISTINCT FROM d.key_version AS rekeyed,
          b.old_hash_version IS DISTINCT FROM d.hash_version AS rehashed
""")


@dataclass(frozen=True)
class Rekeyed:
    rekeyed: int
    rehashed: int


async def rekey_batch(session: AsyncSession, settings: Settings) -> Rekeyed | None:
    """Move up to 100 rows that are not current to the current key and pepper; None when none
    could be moved (all current, missing a key, or locked by another run)."""
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
            "batch": BATCH,
        },
    )
    rows = result.all()
    if not rows:
        return None
    return Rekeyed(sum(1 for r in rows if r.rekeyed), sum(1 for r in rows if r.rehashed))


def _stale(settings: Settings) -> ColumnElement[bool]:
    return (KycDocument.key_version != settings.kyc_key_version) | (
        KycDocument.hash_version != settings.kyc_pepper_version
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


async def unconfigured(session: AsyncSession, settings: Settings) -> list[str]:
    """The stored key and pepper versions with no configured key or pepper, with row counts: those
    numbers cannot be read (key) or matched as duplicates (pepper) until it is configured again."""
    lines: list[str] = []
    for column, configured, what in (
        (KycDocument.key_version, settings.kyc_keys, "key"),
        (KycDocument.hash_version, settings.kyc_peppers, "pepper"),
    ):
        rows = await session.execute(
            select(column, func.count())
            .where(column.is_not(None), column.not_in(list(configured)))
            .group_by(column)
            .order_by(column)
        )
        lines += [
            f"{n} KYC documents under {what} version {version}, which is not configured"
            for version, n in rows.all()
        ]
    return lines
