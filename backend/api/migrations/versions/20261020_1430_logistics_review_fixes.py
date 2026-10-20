"""logistics: a ride flag on deliveries and an index for a route's latest ping (Opus review)

`deliveries.hub_ride`: attempt 1 booked again for a later day because the hub's route left the bag
behind. It rides like a redelivery but it is not a customer miss, so a real miss on that ride still
gets the one free redelivery as attempt 2 (M7 §3.4).

`ix_gps_pings_route_at`: live tracking and the handover read a route's newest ping
(`WHERE route_id = … ORDER BY at DESC LIMIT 1`); the BRIN index on `at` cannot serve that. Made on
the partitioned parent, so every month's partition gets it.

`ix_deliveries_date_status`: the evening jobs read `WHERE delivery_date <= :day AND status IN (…)`
(`hold_expiry`, `schedule_redeliveries`), which the hub-first index cannot serve.

Revision ID: a7c41e5d9b02
Revises: 57fb2f8062ee
Create Date: 2026-10-20 14:30:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c41e5d9b02"
down_revision: str | Sequence[str] | None = "57fb2f8062ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deliveries",
        sa.Column("hub_ride", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="logistics",
    )
    op.execute("CREATE INDEX ix_gps_pings_route_at ON logistics.gps_pings (route_id, at DESC)")
    op.create_index(
        "ix_deliveries_date_status",
        "deliveries",
        ["delivery_date", "status"],
        schema="logistics",
    )


def downgrade() -> None:
    op.drop_index("ix_deliveries_date_status", table_name="deliveries", schema="logistics")
    op.execute("DROP INDEX logistics.ix_gps_pings_route_at")
    op.drop_column("deliveries", "hub_ride", schema="logistics")
