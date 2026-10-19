"""partners: a plain index on assignment_orders.order_id (the short-code lookup reads every status)

Revision ID: e47a19c3d6f2
Revises: b94e7c20a5d1
Create Date: 2026-10-20 09:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e47a19c3d6f2"
down_revision: str | Sequence[str] | None = "b94e7c20a5d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_assignment_orders_order_id",
        "assignment_orders",
        ["order_id"],
        unique=False,
        schema="partners",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assignment_orders_order_id", table_name="assignment_orders", schema="partners"
    )
