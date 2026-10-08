"""subscriptions: weekly_cycles gains reminded_at, so the cut-off reminder goes once (M3 §6)

Revision ID: 08c4162139bc
Revises: a8e9dd07f72c
Create Date: 2026-10-09 09:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "08c4162139bc"
down_revision: str | Sequence[str] | None = "a8e9dd07f72c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscriptions"


def upgrade() -> None:
    op.add_column(
        "weekly_cycles",
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("weekly_cycles", "reminded_at", schema=SCHEMA)
