"""orders: the kitchen that cooks a tiffin, set by M8 (ADR-0026 §3, M4 §3.5)

Revision ID: ce599e5ada4c
Revises: d113d704a94b
Create Date: 2026-10-15 07:13:11.333102+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ce599e5ada4c"
down_revision: str | Sequence[str] | None = "d113d704a94b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("kitchen_partner_id", sa.Uuid(), nullable=True), schema="orders"
    )
    op.create_check_constraint(
        op.f("ck_orders_kitchen"),
        "orders",
        "kind = 'tiffin' OR kitchen_partner_id IS NULL",
        schema="orders",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_orders_kitchen"), "orders", type_="check", schema="orders")
    op.drop_column("orders", "kitchen_partner_id", schema="orders")
