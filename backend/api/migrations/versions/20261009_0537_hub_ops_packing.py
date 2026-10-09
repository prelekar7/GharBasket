"""hub_ops: packing tasks, their lines and the packers' events (M6 §3.2, §4; ADR-0026 §6)

One task per box or add-on order to pack (unique order_id), lines in steps copied from the order,
and an audit row per packer action with the phone's tap time. Order, slot, society, product and
user ids are plain UUIDs (ADR-0026 §5); route_id and stop_seq stay null until M7 publishes routes.

Revision ID: 232e527f6a85
Revises: 386739a6e42e
Create Date: 2026-10-09 05:37:52.790746+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "232e527f6a85"
down_revision: str | Sequence[str] | None = "386739a6e42e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "packing_tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("order_kind", sa.String(length=8), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("tower_id", sa.String(length=20), nullable=True),
        sa.Column("flat", sa.String(length=20), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.Uuid(), nullable=True),
        sa.Column("stop_seq", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(length=8), server_default="queued", nullable=False),
        sa.Column("packer_user_id", sa.Uuid(), nullable=True),
        sa.Column("bags", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("crate_no", sa.SmallInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("packed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "order_kind IN ('box', 'addon')", name=op.f("ck_packing_tasks_order_kind")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'picking', 'packed')", name=op.f("ck_packing_tasks_status")
        ),
        sa.CheckConstraint("bags BETWEEN 1 AND 5", name=op.f("ck_packing_tasks_bags")),
        sa.CheckConstraint(
            "crate_no IS NULL OR crate_no BETWEEN 1 AND 99", name=op.f("ck_packing_tasks_crate_no")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_packing_tasks_version")),
        sa.ForeignKeyConstraint(
            ["hub_id"],
            ["hub_ops.hubs.id"],
            name=op.f("fk_packing_tasks_hub_id_hubs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packing_tasks")),
        sa.UniqueConstraint("order_id", name=op.f("uq_packing_tasks_order_id")),
        schema="hub_ops",
    )
    op.create_index(
        "ix_packing_tasks_queue",
        "packing_tasks",
        ["hub_id", "delivery_date", "status", "seq"],
        unique=False,
        schema="hub_ops",
    )
    op.create_table(
        "packing_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("client_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "action IN ('start', 'pick', 'shortage', 'complete', 'release')",
            name=op.f("ck_packing_events_action"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["hub_ops.packing_tasks.id"],
            name=op.f("fk_packing_events_task_id_packing_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packing_events")),
        schema="hub_ops",
    )
    op.create_index(
        op.f("ix_hub_ops_packing_events_task_id"),
        "packing_events",
        ["task_id"],
        unique=False,
        schema="hub_ops",
    )
    op.create_table(
        "packing_lines",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("step_qty", sa.Integer(), nullable=False),
        sa.Column("step_unit", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("picked_qty", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("short_qty", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("substitute_product_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "picked_qty >= 0 AND short_qty >= 0 AND picked_qty + short_qty <= qty",
            name=op.f("ck_packing_lines_picked"),
        ),
        sa.CheckConstraint("qty >= 1", name=op.f("ck_packing_lines_qty")),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["hub_ops.packing_tasks.id"],
            name=op.f("fk_packing_lines_task_id_packing_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "product_id", name=op.f("pk_packing_lines")),
        schema="hub_ops",
    )


def downgrade() -> None:
    op.drop_table("packing_lines", schema="hub_ops")
    op.drop_index(
        op.f("ix_hub_ops_packing_events_task_id"), table_name="packing_events", schema="hub_ops"
    )
    op.drop_table("packing_events", schema="hub_ops")
    op.drop_index("ix_packing_tasks_queue", table_name="packing_tasks", schema="hub_ops")
    op.drop_table("packing_tasks", schema="hub_ops")
