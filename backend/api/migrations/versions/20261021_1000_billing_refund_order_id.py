"""billing: an order refund the wallet cannot take goes to source: refunds.order_id (M5 §3.3)

Revision ID: 5b8e1c3d7a94
Revises: 3f9a6d1e5b82
Create Date: 2026-10-21 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b8e1c3d7a94"
down_revision: str | Sequence[str] | None = "3f9a6d1e5b82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "reason IN ('customer', 'account_deleted')"
NEW = "reason IN ('customer', 'account_deleted', 'order')"


def upgrade() -> None:
    # M4's order id: no foreign key across schemas.
    op.add_column("refunds", sa.Column("order_id", sa.Uuid(), nullable=True), schema="billing")
    op.drop_constraint(op.f("ck_refunds_reason"), "refunds", schema="billing")
    op.create_check_constraint(op.f("ck_refunds_reason"), "refunds", NEW, schema="billing")


def downgrade() -> None:
    # Refunds are kept for reconciliation, so an order refund is relabelled rather than deleted.
    op.execute("UPDATE billing.refunds SET reason = 'account_deleted' WHERE reason = 'order'")
    op.drop_constraint(op.f("ck_refunds_reason"), "refunds", schema="billing")
    op.create_check_constraint(op.f("ck_refunds_reason"), "refunds", OLD, schema="billing")
    op.drop_column("refunds", "order_id", schema="billing")
