"""partners: tiffin assignment runs, offers to kitchens, their orders and events (M8 §3.4, §4)

Revision ID: 3b85ecb87754
Revises: af74586fec9d
Create Date: 2026-10-14 11:54:38.109227+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3b85ecb87754"
down_revision: str | Sequence[str] | None = "af74586fec9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assignment_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("meal", sa.String(length=8), nullable=False),
        sa.Column("round", sa.SmallInteger(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("model", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("portions", sa.Integer(), nullable=False),
        sa.Column("unassigned", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seconds", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("meal IN ('lunch', 'dinner')", name=op.f("ck_assignment_runs_meal")),
        sa.CheckConstraint("source IN ('ml', 'fallback')", name=op.f("ck_assignment_runs_source")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignment_runs")),
        schema="partners",
    )
    op.create_index(
        "ix_assignment_runs_hub_id_date_meal",
        "assignment_runs",
        ["hub_id", "date", "meal"],
        unique=False,
        schema="partners",
    )
    op.create_table(
        "assignments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("meal", sa.String(length=8), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("round", sa.SmallInteger(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("portions", sa.Integer(), nullable=False),
        sa.Column("respond_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.String(length=16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checklist", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("late", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decline_reason IS NULL OR decline_reason IN ('unwell', 'no_ingredients', 'family', 'other')",
            name=op.f("ck_assignments_decline_reason"),
        ),
        sa.CheckConstraint("meal IN ('lunch', 'dinner')", name=op.f("ck_assignments_meal")),
        sa.CheckConstraint(
            "source IN ('ml', 'fallback', 'manual')", name=op.f("ck_assignments_source")
        ),
        sa.CheckConstraint(
            "status IN ('offered', 'accepted', 'preparing', 'ready', 'completed', 'declined', 'expired', 'cancelled')",
            name=op.f("ck_assignments_status"),
        ),
        sa.CheckConstraint("portions > 0", name=op.f("ck_assignments_portions")),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partners.partners.id"],
            name=op.f("fk_assignments_partner_id_partners"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["partners.assignment_runs.id"],
            name=op.f("fk_assignments_run_id_assignment_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignments")),
        schema="partners",
    )
    op.create_index(
        "ix_assignments_hub_id_date_status",
        "assignments",
        ["hub_id", "date", "status"],
        unique=False,
        schema="partners",
    )
    op.create_index(
        "ix_assignments_partner_id_date",
        "assignments",
        ["partner_id", "date"],
        unique=False,
        schema="partners",
    )
    op.create_table(
        "assignment_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("client_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["partners.assignments.id"],
            name=op.f("fk_assignment_events_assignment_id_assignments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignment_events")),
        schema="partners",
    )
    op.create_index(
        op.f("ix_partners_assignment_events_assignment_id"),
        "assignment_events",
        ["assignment_id"],
        unique=False,
        schema="partners",
    )
    op.create_table(
        "assignment_orders",
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("portions", sa.SmallInteger(), nullable=False),
        sa.Column("short_code", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="assigned", nullable=False),
        sa.Column("km", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.CheckConstraint(
            "status IN ('assigned', 'ready', 'picked_up', 'delivered', 'failed', 'released')",
            name=op.f("ck_assignment_orders_status"),
        ),
        sa.CheckConstraint("portions > 0", name=op.f("ck_assignment_orders_portions")),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["partners.assignments.id"],
            name=op.f("fk_assignment_orders_assignment_id_assignments"),
        ),
        sa.PrimaryKeyConstraint("assignment_id", "order_id", name=op.f("pk_assignment_orders")),
        schema="partners",
    )
    op.create_index(
        "uq_assignment_orders_order_id_held",
        "assignment_orders",
        ["order_id"],
        unique=True,
        schema="partners",
        postgresql_where=sa.text("status <> 'released'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_assignment_orders_order_id_held",
        table_name="assignment_orders",
        schema="partners",
        postgresql_where=sa.text("status <> 'released'"),
    )
    op.drop_table("assignment_orders", schema="partners")
    op.drop_index(
        op.f("ix_partners_assignment_events_assignment_id"),
        table_name="assignment_events",
        schema="partners",
    )
    op.drop_table("assignment_events", schema="partners")
    op.drop_index("ix_assignments_partner_id_date", table_name="assignments", schema="partners")
    op.drop_index("ix_assignments_hub_id_date_status", table_name="assignments", schema="partners")
    op.drop_table("assignments", schema="partners")
    op.drop_index(
        "ix_assignment_runs_hub_id_date_meal", table_name="assignment_runs", schema="partners"
    )
    op.drop_table("assignment_runs", schema="partners")
