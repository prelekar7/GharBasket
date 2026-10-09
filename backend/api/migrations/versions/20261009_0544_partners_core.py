"""partners: the partner core, one record per verified person and kind, without society_id
(M8 §3.3, §4, build order (a); ADR-0026 §4)

Revision ID: c19ddf09f701
Revises: 4f1c2a7d9e30
Create Date: 2026-10-09 05:44:04.689469+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c19ddf09f701"
down_revision: str | Sequence[str] | None = "4f1c2a7d9e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "partners",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("display_name", sa.String(length=40), nullable=False),
        sa.Column("photo_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("rating_avg", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("rating_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspend_reason_code", sa.String(length=16), nullable=True),
        sa.Column("suspend_note", sa.String(length=300), nullable=True),
        sa.Column("data_source", sa.String(length=16), server_default="real", nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_partners_data_source")
        ),
        sa.CheckConstraint(
            "kind IN ('kitchen', 'captain', 'rider', 'packer')", name=op.f("ck_partners_kind")
        ),
        sa.CheckConstraint(
            "status <> 'suspended' OR (suspended_at IS NOT NULL AND suspend_reason_code IS NOT NULL)",
            name=op.f("ck_partners_suspension"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'suspended', 'left')", name=op.f("ck_partners_status")
        ),
        sa.CheckConstraint(
            "suspend_reason_code IS NULL OR suspend_reason_code IN ('rating', 'hygiene', 'compliance', 'conduct', 'other', 'left')",
            name=op.f("ck_partners_suspend_reason_code"),
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 2 AND 40", name=op.f("ck_partners_display_name")
        ),
        sa.CheckConstraint("rating_count >= 0", name=op.f("ck_partners_rating_count")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partners")),
        sa.UniqueConstraint("user_id", "kind", name=op.f("uq_partners_user_id_kind")),
        schema="partners",
    )
    op.create_index(
        "ix_partners_hub_id_kind_status",
        "partners",
        ["hub_id", "kind", "status"],
        unique=False,
        schema="partners",
    )


def downgrade() -> None:
    op.drop_index("ix_partners_hub_id_kind_status", table_name="partners", schema="partners")
    op.drop_table("partners", schema="partners")
