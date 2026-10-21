"""orders: carry_overs, the kept non-perishables of a box missed twice (M4 §3.8, §4)

Revision ID: 5b8d2e91c4a7
Revises: e47a19c3d6f2
Create Date: 2026-10-20 17:45:55.697319+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b8d2e91c4a7"
down_revision: str | Sequence[str] | None = "e47a19c3d6f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "carry_overs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("from_order_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("step_qty", sa.Integer(), nullable=False),
        sa.Column("step_unit", sa.String(length=8), nullable=False),
        sa.Column("paid_paise", sa.BigInteger(), nullable=False),
        sa.Column("missed_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("to_order_id", sa.Uuid(), nullable=True),
        sa.Column("returned_in_order_id", sa.Uuid(), nullable=True),
        sa.Column("refunded_by", sa.String(length=6), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "refunded_by IS NULL OR refunded_by IN ('expiry', 'hub')",
            name=op.f("ck_carry_overs_refunded_by"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'attached', 'packed', 'delivered', 'refunded')",
            name=op.f("ck_carry_overs_status"),
        ),
        sa.CheckConstraint(
            "steps > 0 AND step_qty > 0 AND paid_paise >= 0", name=op.f("ck_carry_overs_amounts")
        ),
        sa.ForeignKeyConstraint(
            ["from_order_id"],
            ["orders.orders.id"],
            name=op.f("fk_carry_overs_from_order_id_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["returned_in_order_id"],
            ["orders.orders.id"],
            name=op.f("fk_carry_overs_returned_in_order_id_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["to_order_id"], ["orders.orders.id"], name=op.f("fk_carry_overs_to_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carry_overs")),
        sa.UniqueConstraint(
            "from_order_id", "product_id", name=op.f("uq_carry_overs_from_order_id_product_id")
        ),
        schema="orders",
    )
    op.create_index(
        "ix_carry_overs_household_id_status",
        "carry_overs",
        ["household_id", "status"],
        unique=False,
        schema="orders",
    )
    op.create_index(
        "ix_carry_overs_to_order_id", "carry_overs", ["to_order_id"], unique=False, schema="orders"
    )


def downgrade() -> None:
    op.drop_index("ix_carry_overs_to_order_id", table_name="carry_overs", schema="orders")
    op.drop_index("ix_carry_overs_household_id_status", table_name="carry_overs", schema="orders")
    op.drop_table("carry_overs", schema="orders")
