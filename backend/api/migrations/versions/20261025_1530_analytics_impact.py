"""analytics: mv_impact_monthly gains what an NGO received (M12 §3.6)

The NGO rows of `mv_impact_monthly` counted hand-offs, meals and food but not the items and the
kilograms of an items hand-off, which the NGO's impact report (`ngos[]`, the NGO scope's totals)
needs. Two columns are added: `handoff_items` (pieces, and kilograms as one item per kg) and
`handoff_kg_tenths` (the kilograms of every kind of hand-off). The view is dropped and made again
(a materialised view cannot gain a column), with the same grain and unique index, so
`REFRESH MATERIALIZED VIEW CONCURRENTLY` still works. It reads `analytics` tables only.

Revision ID: 5b1d8c0a47e2
Revises: 5b8e1d3a7c42
Create Date: 2026-10-25 15:30:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b1d8c0a47e2"
down_revision: str | Sequence[str] | None = "5b8e1d3a7c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The view is SQL built from the constants below (no input reaches it): S608 does not apply.
# ruff: noqa: S608

VIEW = "mv_impact_monthly"
GRAIN = "hub_id, ngo_id, month, data_source"

# What the view had (f5199f44ecf8), restored by the downgrade.
OLD_COLUMNS = """
    count(*) FILTER (WHERE kind = 'donation_verified')::bigint AS donations_verified,
    coalesce(sum(items) FILTER (WHERE kind = 'donation_verified'), 0)::bigint AS items_donated,
    coalesce(sum(kg_tenths) FILTER (WHERE kind = 'donation_verified'), 0)::bigint
        AS kg_tenths_donated,
    count(DISTINCT household_id) FILTER (WHERE kind = 'donation_verified')::bigint
        AS households_donating,
    coalesce(sum(meals) FILTER (WHERE kind = 'meals_pledged'), 0)::bigint AS meals_pledged,
    coalesce(sum(meals) FILTER (WHERE kind = 'handoff_meals'), 0)::bigint AS meals_served,
    coalesce(sum(kg_tenths) FILTER (WHERE kind = 'handoff_food'), 0)::bigint AS food_kg_tenths,
    count(*) FILTER (WHERE kind LIKE 'handoff_%')::bigint AS handoffs,
    sum(people_reached)::bigint AS people_reached,
    count(*) FILTER (WHERE kind = 'referral_converted')::bigint AS referrals_converted,
    count(*) FILTER (WHERE kind = 'society_live')::bigint AS societies_live
"""
NEW_COLUMNS = (
    OLD_COLUMNS.rstrip()
    + """,
    coalesce(sum(items) FILTER (WHERE kind LIKE 'handoff_%'), 0)::bigint AS handoff_items,
    coalesce(sum(kg_tenths) FILTER (WHERE kind LIKE 'handoff_%'), 0)::bigint AS handoff_kg_tenths
"""
)


def _select(columns: str) -> str:
    # Hub totals (ngo_id null), then one row per NGO for what it received.
    return f"""
SELECT hub_id, NULL::uuid AS ngo_id, date_trunc('month', date)::date AS month, data_source,
    {columns}
FROM analytics.fact_community GROUP BY 1, 2, 3, 4
UNION ALL
SELECT hub_id, ngo_id, date_trunc('month', date)::date AS month, data_source,
    {columns}
FROM analytics.fact_community WHERE ngo_id IS NOT NULL GROUP BY 1, 2, 3, 4
"""


def _remake(columns: str) -> None:
    op.execute(f"DROP MATERIALIZED VIEW analytics.{VIEW}")
    op.execute(f"CREATE MATERIALIZED VIEW analytics.{VIEW} AS {_select(columns)}")
    op.execute(
        f"CREATE UNIQUE INDEX uq_{VIEW}_grain ON analytics.{VIEW} ({GRAIN}) NULLS NOT DISTINCT"
    )


def upgrade() -> None:
    _remake(NEW_COLUMNS)


def downgrade() -> None:
    _remake(OLD_COLUMNS)
