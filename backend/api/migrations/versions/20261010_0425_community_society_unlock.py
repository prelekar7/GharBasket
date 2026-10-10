"""community: how societies unlock: live_at, the pinned threshold and where it came from
(M9 §3.6, §4)

Revision ID: 2ca234f73a31
Revises: 6414c91b9017
Create Date: 2026-10-10 04:25:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2ca234f73a31"
down_revision: str | Sequence[str] | None = "6414c91b9017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "societies",
        sa.Column("live_at", sa.DateTime(timezone=True), nullable=True),
        schema="community",
    )
    op.add_column(
        "societies",
        sa.Column(
            "unlock_threshold_pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        schema="community",
    )
    op.add_column(
        "societies",
        sa.Column(
            "threshold_source", sa.String(length=8), server_default="default", nullable=False
        ),
        schema="community",
    )
    op.add_column(
        "societies",
        sa.Column("threshold_computed_at", sa.DateTime(timezone=True), nullable=True),
        schema="community",
    )
    op.create_check_constraint(
        op.f("ck_societies_threshold_source"),
        "societies",
        "threshold_source IN ('default', 'model', 'hub')",
        schema="community",
    )
    # Societies already live have always been: their boxes pay no pre-live fee.
    op.execute("UPDATE community.societies SET live_at = updated_at WHERE status = 'live'")


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_societies_threshold_source"), "societies", schema="community", type_="check"
    )
    op.drop_column("societies", "threshold_computed_at", schema="community")
    op.drop_column("societies", "threshold_source", schema="community")
    op.drop_column("societies", "unlock_threshold_pinned", schema="community")
    op.drop_column("societies", "live_at", schema="community")
