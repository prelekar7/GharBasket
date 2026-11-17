"""billing: every payment records its Razorpay mode, test or live (M5 §3.10, ADR-0037)

`billing.payments.mode` is `test` or `live`: the RAZORPAY_MODE the payment was made in. Every
existing row, dev payments included, was made with test keys (live keys were refused until now),
so the column's default backfills them `test`. The default stays, so the previous release can
still write rows during a deploy; the new code always sets the mode itself.

Revision ID: 34108e224d3b
Revises: 2b9e6c4d8f17
Create Date: 2026-11-15 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "34108e224d3b"
down_revision: str | Sequence[str] | None = "2b9e6c4d8f17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "billing"


def upgrade() -> None:
    # A constant default: Postgres fills every existing row with it without rewriting the table.
    op.add_column(
        "payments",
        sa.Column("mode", sa.Text(), server_default="test", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_payments_mode"), "payments", "mode IN ('test', 'live')", schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_payments_mode"), "payments", schema=SCHEMA)
    op.drop_column("payments", "mode", schema=SCHEMA)
