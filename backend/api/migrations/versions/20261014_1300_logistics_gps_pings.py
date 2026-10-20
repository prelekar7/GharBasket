"""logistics: GPS pings, partitioned by month (M7 §3.3, §4; handbook 13 §13.5)

Accepted positions of riders on in-progress routes, kept 90 days. The key is the rider and the
time, so a replayed ping inserts nothing. Partitioned by range on `at`: the API makes a month's
partition (`gps_pings_YYYYMM`) with its first ping and `purge_gps_pings` drops it once all its
pings are older than 90 days. A BRIN index on `at` keeps time-range reads cheap.

Revision ID: 942627abf5d2
Revises: 05c7cd455ca0
Create Date: 2026-10-14 13:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "942627abf5d2"
down_revision: str | Sequence[str] | None = "05c7cd455ca0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Alembic cannot create a partitioned table, so this is plain SQL; the model matches it.
    op.execute(
        """
        CREATE TABLE logistics.gps_pings (
            rider_user_id uuid NOT NULL,
            at timestamptz NOT NULL,
            route_id uuid NOT NULL,
            location geography(POINT, 4326) NOT NULL,
            accuracy_m smallint NOT NULL,
            speed_mps double precision,
            heading smallint,
            CONSTRAINT pk_gps_pings PRIMARY KEY (rider_user_id, at)
        ) PARTITION BY RANGE (at)
        """
    )
    op.execute("CREATE INDEX ix_gps_pings_at ON logistics.gps_pings USING brin (at)")


def downgrade() -> None:
    op.execute("DROP TABLE logistics.gps_pings")  # and its partitions
