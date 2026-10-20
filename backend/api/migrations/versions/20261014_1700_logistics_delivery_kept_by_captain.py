"""logistics: deliveries.kept_by_captain — a redelivered bag that spent the night with the captain

Attempt 2 of a held bag nobody collected: the next morning's van carries nothing for it, but the
society still gets a stop and the handover moves it to at_society (M7 §3.4, ADR-0027 §3).

Revision ID: 57fb2f8062ee
Revises: 760cc84da6ad
Create Date: 2026-10-14 17:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "57fb2f8062ee"
down_revision: str | Sequence[str] | None = "760cc84da6ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deliveries",
        sa.Column("kept_by_captain", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="logistics",
    )


def downgrade() -> None:
    op.drop_column("deliveries", "kept_by_captain", schema="logistics")
