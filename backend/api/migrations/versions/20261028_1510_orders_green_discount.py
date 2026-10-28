"""orders: discount_code names a box's discount, the green-slot saving (M4 §3.9, M10 §3.8)

Nullable, so existing orders need no backfill: none has a discount today.

Revision ID: 4e81b0c95a27
Revises: 9c2f7a41d6e3
Create Date: 2026-10-28 15:10:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e81b0c95a27"
down_revision: str | Sequence[str] | None = "9c2f7a41d6e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("discount_code", sa.String(length=16), nullable=True), schema="orders"
    )
    op.create_check_constraint(
        op.f("ck_orders_discount_code"),
        "orders",
        "discount_code IS NULL OR discount_code IN ('green_slot')",
        schema="orders",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_orders_discount_code"), "orders", schema="orders")
    op.drop_column("orders", "discount_code", schema="orders")
