"""orders: tiffin slots by meal, and tiffin orders' menu, preferred cook and portions (M4 §3.5)

Revision ID: d113d704a94b
Revises: 483e9f3cf1cf
Create Date: 2026-10-14 20:10:04.927872+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d113d704a94b"
down_revision: str | Sequence[str] | None = "483e9f3cf1cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("tiffin_menu_id", sa.Uuid(), nullable=True), schema="orders")
    op.add_column(
        "orders", sa.Column("preferred_partner_id", sa.Uuid(), nullable=True), schema="orders"
    )
    op.add_column(
        "orders", sa.Column("portions", sa.SmallInteger(), nullable=True), schema="orders"
    )
    op.add_column("slots", sa.Column("meal", sa.String(length=8), nullable=True), schema="orders")
    # Tiffin slots made before this change: a slot starting before 16:00 is lunch.
    op.execute(
        "UPDATE orders.slots SET meal = CASE WHEN start_time < '16:00' THEN 'lunch' ELSE 'dinner'"
        " END WHERE kind = 'tiffin'"
    )
    op.create_check_constraint(
        op.f("ck_slots_meal"),
        "slots",
        "(kind = 'tiffin' AND meal IN ('lunch', 'dinner')) OR (kind <> 'tiffin' AND meal IS NULL)",
        schema="orders",
    )
    op.create_check_constraint(
        op.f("ck_orders_tiffin_only"),
        "orders",
        "kind = 'tiffin' OR (tiffin_menu_id IS NULL AND preferred_partner_id IS NULL"
        " AND portions IS NULL)",
        schema="orders",
    )
    op.create_check_constraint(
        op.f("ck_orders_portions"),
        "orders",
        "portions IS NULL OR portions BETWEEN 1 AND 12",
        schema="orders",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_orders_portions"), "orders", type_="check", schema="orders")
    op.drop_constraint(op.f("ck_orders_tiffin_only"), "orders", type_="check", schema="orders")
    op.drop_constraint(op.f("ck_slots_meal"), "slots", type_="check", schema="orders")
    op.drop_column("slots", "meal", schema="orders")
    op.drop_column("orders", "portions", schema="orders")
    op.drop_column("orders", "preferred_partner_id", schema="orders")
    op.drop_column("orders", "tiffin_menu_id", schema="orders")
