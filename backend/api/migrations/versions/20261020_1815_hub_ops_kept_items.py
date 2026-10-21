"""hub_ops: kept items, returned bags and the kept item a stock move released (M6 §3.5, §4)

Revision ID: 7c2e4a90d513
Revises: 5b8d2e91c4a7
Create Date: 2026-10-20 18:15:29.474919+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c2e4a90d513"
down_revision: str | Sequence[str] | None = "5b8d2e91c4a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "returned_bags",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("scanned_by", sa.Uuid(), nullable=False),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["hub_id"],
            ["hub_ops.hubs.id"],
            name=op.f("fk_returned_bags_hub_id_hubs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_returned_bags")),
        schema="hub_ops",
    )
    op.create_table(
        "kept_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("carry_id", sa.Uuid(), nullable=False),
        sa.Column("from_order_id", sa.Uuid(), nullable=False),
        sa.Column("bag_order_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("tower_id", sa.String(length=20), nullable=True),
        sa.Column("flat", sa.String(length=20), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("step_qty", sa.Integer(), nullable=False),
        sa.Column("step_unit", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=8), server_default="kept", nullable=False),
        sa.Column("packed_task_id", sa.Uuid(), nullable=True),
        sa.Column(
            "kept_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('kept', 'packed', 'released', 'lost')", name=op.f("ck_kept_items_status")
        ),
        sa.CheckConstraint(
            "step_unit IN ('g', 'kg', 'ml', 'l', 'piece', 'bunch', 'pack')",
            name=op.f("ck_kept_items_step_unit"),
        ),
        sa.CheckConstraint("qty >= 1 AND step_qty >= 1", name=op.f("ck_kept_items_qty")),
        sa.ForeignKeyConstraint(
            ["hub_id"],
            ["hub_ops.hubs.id"],
            name=op.f("fk_kept_items_hub_id_hubs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["packed_task_id"],
            ["hub_ops.packing_tasks.id"],
            name=op.f("fk_kept_items_packed_task_id_packing_tasks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kept_items")),
        sa.UniqueConstraint(
            "carry_id", "bag_order_id", name=op.f("uq_kept_items_carry_id_bag_order_id")
        ),
        schema="hub_ops",
    )
    op.create_index(
        "ix_kept_items_household_id", "kept_items", ["household_id"], unique=False, schema="hub_ops"
    )
    op.create_index(
        "ix_kept_items_hub_id_status",
        "kept_items",
        ["hub_id", "status"],
        unique=False,
        schema="hub_ops",
    )
    op.add_column(
        "stock_moves", sa.Column("kept_item_id", sa.Uuid(), nullable=True), schema="hub_ops"
    )
    op.create_foreign_key(
        op.f("fk_stock_moves_kept_item_id_kept_items"),
        "stock_moves",
        "kept_items",
        ["kept_item_id"],
        ["id"],
        source_schema="hub_ops",
        referent_schema="hub_ops",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_stock_moves_kept_item_id_kept_items"),
        "stock_moves",
        schema="hub_ops",
        type_="foreignkey",
    )
    op.drop_column("stock_moves", "kept_item_id", schema="hub_ops")
    op.drop_index("ix_kept_items_hub_id_status", table_name="kept_items", schema="hub_ops")
    op.drop_index("ix_kept_items_household_id", table_name="kept_items", schema="hub_ops")
    op.drop_table("kept_items", schema="hub_ops")
    op.drop_table("returned_bags", schema="hub_ops")
