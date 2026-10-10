"""hub_ops: hubs gain pre_live_box_fee_paise; community: societies gain live_at (M9 §3.6, §11)

Boxes are allowed before a society goes live, with a per-box delivery fee set per hub (default
3,000 paise, 0 to 10,000). The fee stops once the society's live_at is set, which happens once
in its life. Societies that are live today get live_at now: when they went live is not known.

Revision ID: 0874299d92b5
Revises: 232e527f6a85
Create Date: 2026-10-10 06:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0874299d92b5"
down_revision: str | Sequence[str] | None = "232e527f6a85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hubs",
        sa.Column(
            "pre_live_box_fee_paise",
            sa.BigInteger(),
            server_default=sa.text("3000"),
            nullable=False,
        ),
        schema="hub_ops",
    )
    op.create_check_constraint(
        op.f("ck_hubs_pre_live_box_fee_paise"),
        "hubs",
        "pre_live_box_fee_paise BETWEEN 0 AND 10000",
        schema="hub_ops",
    )
    op.add_column(
        "societies",
        sa.Column("live_at", sa.DateTime(timezone=True), nullable=True),
        schema="community",
    )
    op.execute("UPDATE community.societies SET live_at = now() WHERE status = 'live'")


def downgrade() -> None:
    op.drop_column("societies", "live_at", schema="community")
    op.drop_constraint(op.f("ck_hubs_pre_live_box_fee_paise"), "hubs", schema="hub_ops")
    op.drop_column("hubs", "pre_live_box_fee_paise", schema="hub_ops")
