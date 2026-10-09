"""partners: applications to become a partner, one open application per person (M8 §3.1, §4)

Revision ID: 931fd98ea0ff
Revises: 058901d53044
Create Date: 2026-10-09 12:19:29.735794+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "931fd98ea0ff"
down_revision: str | Sequence[str] | None = "058901d53044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="applied", nullable=False),
        sa.Column("docs_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("partner_id", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_source", sa.String(length=16), server_default="real", nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_applications_data_source")
        ),
        sa.CheckConstraint(
            "kind IN ('kitchen', 'captain', 'rider', 'packer')", name=op.f("ck_applications_kind")
        ),
        sa.CheckConstraint(
            "scope_type IN ('society', 'hub')", name=op.f("ck_applications_scope_type")
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR (partner_id IS NOT NULL AND verified_at IS NOT NULL)",
            name=op.f("ck_applications_verified"),
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'kyc_pending', 'training', 'verified', 'rejected', 'withdrawn')",
            name=op.f("ck_applications_status"),
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 2 AND 40", name=op.f("ck_applications_display_name")
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partners.partners.id"],
            name=op.f("fk_applications_partner_id_partners"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        schema="partners",
    )
    op.create_index(
        "ix_applications_hub_id_status_created_at",
        "applications",
        ["hub_id", "status", "created_at"],
        unique=False,
        schema="partners",
    )
    op.create_index(
        op.f("ix_partners_applications_user_id"),
        "applications",
        ["user_id"],
        unique=False,
        schema="partners",
    )
    op.create_index(
        "uq_applications_user_id_open",
        "applications",
        ["user_id"],
        unique=True,
        schema="partners",
        postgresql_where=sa.text("status IN ('applied', 'kyc_pending', 'training')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_applications_user_id_open",
        table_name="applications",
        schema="partners",
        postgresql_where=sa.text("status IN ('applied', 'kyc_pending', 'training')"),
    )
    op.drop_index(
        op.f("ix_partners_applications_user_id"), table_name="applications", schema="partners"
    )
    op.drop_index(
        "ix_applications_hub_id_status_created_at", table_name="applications", schema="partners"
    )
    op.drop_table("applications", schema="partners")
