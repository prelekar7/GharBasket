"""partners: kyc_documents.hash_version is set exactly when number_hash is (M8 §3.1, contract)

The contract step after `20261112_1400_partners_kyc_hash_version`: every API now running writes
`hash_version` with each hash, so a last backfill catches any row the old API wrote during that
deploy, and the check `(number_hash IS NULL) = (hash_version IS NULL)` follows. The code's
"a NULL hash_version is pepper 1" fallbacks go in the same PR.

Revision ID: 2b9e6c4d8f17
Revises: 5d2f8a1c7e46
Create Date: 2026-11-13 15:30:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2b9e6c4d8f17"
down_revision: str | Sequence[str] | None = "5d2f8a1c7e46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "partners"


def upgrade() -> None:
    # Only pepper 1 existed before the column, so a hash without a version is version 1.
    op.execute(
        "UPDATE partners.kyc_documents SET hash_version = 1 "
        "WHERE number_hash IS NOT NULL AND hash_version IS NULL"
    )
    op.create_check_constraint(
        op.f("ck_kyc_documents_hash_version"),
        "kyc_documents",
        "(number_hash IS NULL) = (hash_version IS NULL)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_kyc_documents_hash_version"), "kyc_documents", schema=SCHEMA)
