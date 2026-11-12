"""partners: kyc_documents.hash_version, the pepper version of number_hash (M8 §3.1 rotation)

Revision ID: 8e4b2d6f1a93
Revises: 3c7a9e1f5b28
Create Date: 2026-11-12 14:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e4b2d6f1a93"
down_revision: str | Sequence[str] | None = "3c7a9e1f5b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "partners"


def upgrade() -> None:
    op.add_column(
        "kyc_documents", sa.Column("hash_version", sa.SmallInteger(), nullable=True), schema=SCHEMA
    )
    # Every hash so far was made with the only pepper there was, version 1.
    op.execute("UPDATE partners.kyc_documents SET hash_version = 1 WHERE number_hash IS NOT NULL")
    op.create_check_constraint(
        op.f("ck_kyc_documents_hash_version"),
        "kyc_documents",
        "(number_hash IS NULL) = (hash_version IS NULL)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Hashes made under a later pepper no longer match after this, as before the column existed;
    # downgrade only before rotating the pepper.
    op.drop_constraint(op.f("ck_kyc_documents_hash_version"), "kyc_documents", schema=SCHEMA)
    op.drop_column("kyc_documents", "hash_version", schema=SCHEMA)
