"""orders: cycle_id unique only among orders that are not cancelled; subscriptions: a cycle can be
payment_failed (M4 §3.4, §4; M3 §3.3)

A skip cancels the week's order, and an un-skipped week approved again gets a new one, so the
unique constraint on `orders.orders.cycle_id` becomes a partial unique index. The weekly cycle
mirrors a box the cut-off could not debit as `payment_failed`.

Revision ID: 15f45f1073b6
Revises: 4f1c2a7d9e30
Create Date: 2026-10-09 05:43:43.944313+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "15f45f1073b6"
down_revision: str | Sequence[str] | None = "4f1c2a7d9e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CYCLE_STATUSES = "'draft', 'awaiting_approval', 'approved', 'skipped', 'cancelled', 'locked'"


def upgrade() -> None:
    op.drop_constraint(op.f("uq_orders_cycle_id"), "orders", schema="orders", type_="unique")
    op.create_index(
        "uq_orders_cycle_id_active",
        "orders",
        ["cycle_id"],
        unique=True,
        schema="orders",
        postgresql_where=sa.text("status <> 'cancelled'"),
    )
    op.drop_constraint(op.f("ck_weekly_cycles_status"), "weekly_cycles", schema="subscriptions")
    op.create_check_constraint(
        op.f("ck_weekly_cycles_status"),
        "weekly_cycles",
        f"status IN ({CYCLE_STATUSES}, 'payment_failed')",
        schema="subscriptions",
    )


def downgrade() -> None:
    # A payment_failed week was approved before the cut-off; its order keeps the truth.
    op.execute(
        "UPDATE subscriptions.weekly_cycles SET status = 'approved' WHERE status = 'payment_failed'"
    )
    op.drop_constraint(op.f("ck_weekly_cycles_status"), "weekly_cycles", schema="subscriptions")
    op.create_check_constraint(
        op.f("ck_weekly_cycles_status"),
        "weekly_cycles",
        f"status IN ({CYCLE_STATUSES})",
        schema="subscriptions",
    )
    # Only one order per cycle may keep the link: older cancelled ones let go of it.
    op.execute(
        """
        UPDATE orders.orders AS o SET cycle_id = NULL
        WHERE o.status = 'cancelled' AND EXISTS (
            SELECT 1 FROM orders.orders AS other
            WHERE other.cycle_id = o.cycle_id AND other.id <> o.id
              AND (other.status <> 'cancelled' OR other.id > o.id)
        )
        """
    )
    op.drop_index("uq_orders_cycle_id_active", table_name="orders", schema="orders")
    op.create_unique_constraint(op.f("uq_orders_cycle_id"), "orders", ["cycle_id"], schema="orders")
