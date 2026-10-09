"""community: captain appointments, at most one active per society and one society per
captain (M9 §3.2, §4; ADR-0026 §4)

Revision ID: 058901d53044
Revises: c19ddf09f701
Create Date: 2026-10-09 06:16:48.063913+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "058901d53044"
down_revision: str | Sequence[str] | None = "c19ddf09f701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "captains",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("appointed_by", sa.String(length=8), nullable=False),
        sa.Column("appointed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("end_reason", sa.String(length=16), nullable=True),
        sa.Column("ended_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'ended') = (until IS NOT NULL AND end_reason IS NOT NULL)",
            name=op.f("ck_captains_ended"),
        ),
        sa.CheckConstraint(
            "appointed_by IN ('auto', 'hub')", name=op.f("ck_captains_appointed_by")
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('stepped_down', 'replaced', 'hub_decision', 'suspended')",
            name=op.f("ck_captains_end_reason"),
        ),
        sa.CheckConstraint("status IN ('active', 'ended')", name=op.f("ck_captains_status")),
        sa.ForeignKeyConstraint(
            ["society_id"],
            ["community.societies.id"],
            name=op.f("fk_captains_society_id_societies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_captains")),
        schema="community",
    )
    op.create_index(
        "ix_captains_society_id_since",
        "captains",
        ["society_id", "since"],
        unique=False,
        schema="community",
    )
    op.create_index(
        "ix_captains_user_id", "captains", ["user_id"], unique=False, schema="community"
    )
    op.create_index(
        "uq_captains_active_partner",
        "captains",
        ["partner_id"],
        unique=True,
        schema="community",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_captains_active_society",
        "captains",
        ["society_id"],
        unique=True,
        schema="community",
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_captains_active_society",
        table_name="captains",
        schema="community",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        "uq_captains_active_partner",
        table_name="captains",
        schema="community",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index("ix_captains_user_id", table_name="captains", schema="community")
    op.drop_index("ix_captains_society_id_since", table_name="captains", schema="community")
    op.drop_table("captains", schema="community")
