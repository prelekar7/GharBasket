"""identity: pending role grants for numbers that have not signed in, and a trigram index on
user names for the admin lookup (M1 §3.6)

Revision ID: 3e9a4c71b5d8
Revises: 5b8e1d3a7c42
Create Date: 2026-10-25 18:02:41.703806+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3e9a4c71b5d8"
down_revision: str | Sequence[str] | None = "5b8e1d3a7c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_role_grants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=True),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope_type IS NULL AND scope_id IS NULL) OR "
            "(scope_type IN ('society', 'hub', 'ngo') AND scope_id IS NOT NULL)",
            name=op.f("ck_pending_role_grants_scope"),
        ),
        sa.CheckConstraint(
            "phone_e164 ~ '^\\+[1-9][0-9]{7,14}$'",
            name=op.f("ck_pending_role_grants_phone_e164"),
        ),
        sa.CheckConstraint(
            "role IN ('hub_manager', 'franchise_owner', 'ngo_coordinator', 'catalog_editor')",
            name=op.f("ck_pending_role_grants_role"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_role_grants")),
        sa.UniqueConstraint(
            "phone_e164",
            "role",
            "scope_type",
            "scope_id",
            name=op.f("uq_pending_role_grants_phone_e164_role_scope_type_scope_id"),
            postgresql_nulls_not_distinct=True,
        ),
        schema="identity",
    )
    op.create_index(
        "ix_pending_role_grants_expires_at",
        "pending_role_grants",
        ["expires_at"],
        schema="identity",
    )
    op.create_index(
        "ix_users_name_trgm",
        "users",
        ["name"],
        schema="identity",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_users_name_trgm",
        table_name="users",
        schema="identity",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.drop_table("pending_role_grants", schema="identity")
