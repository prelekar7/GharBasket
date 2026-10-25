"""analytics: fact tables, assumptions, refresh runs and the materialised views (M12 §3.2)

The fact tables are M12's own copy of the source modules' facts, pulled through their facades
(M12 §3.1); the views are built from `analytics` tables only, so no other module's migration is
ever blocked by them.

Revision ID: f5199f44ecf8
Revises: 5b8e1c3d7a94
Create Date: 2026-10-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5199f44ecf8"
down_revision: str | Sequence[str] | None = "5b8e1c3d7a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The default assumptions (hub_id null; M12 §3.4, decided 2026-10-09).
DEFAULTS = {
    "aov_paise": 62_000,
    "margin_bp": 2_400,
    "delivery_paise": 4_000,
    "packing_paise": 3_000,
    "captain_paise": 600,
    "payment_fee_bp": 120,
    "cac_paise": 1_300,
}

# The views are SQL built from the constants below (no input reaches them): S608 does not apply.
# ruff: noqa: S608

# --- the materialised views (§3.2), over analytics tables only -------------------------------
# Each has a unique index on its grain (NULLS NOT DISTINCT where a grain column may be null), so
# `REFRESH MATERIALIZED VIEW CONCURRENTLY` works and reads never block.

# The order statuses after packing: what counts as packed lines (boxes and add-ons; a box is
# refunded only after a failed delivery).
PACKED = (
    "('packed', 'dispatched', 'at_society', 'delivered', 'undelivered', 'redelivery', 'refunded')"
)
# A cycle the customer or auto-approve approved (a later lock or failed debit keeps it approved).
APPROVED = "('approved', 'locked', 'payment_failed')"
KEY = ("hub_id", "date", "data_source")

# (CTE, its SELECT over one fact table grouped by the key, its metric columns)
DAILY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "o",
        f"""
        SELECT hub_id, delivery_date AS date, data_source,
            count(*) FILTER (WHERE status = 'delivered') AS orders_delivered,
            count(*) FILTER (WHERE status = 'delivered' AND kind = 'box') AS boxes_delivered,
            count(DISTINCT household_id) FILTER (WHERE status = 'delivered') AS households_active,
            sum(total_paise - refunded_paise - credited_paise)
                FILTER (WHERE status = 'delivered') AS gmv_paise,
            sum(refunded_paise + credited_paise) AS refunds_paise,
            count(*) FILTER (WHERE on_time) AS on_time_orders,
            count(*) FILTER (WHERE first_attempt) AS first_attempt_orders,
            count(*) FILTER (WHERE first_attempt IS NOT NULL) AS dispatched_orders,
            sum(short_lines) FILTER (WHERE kind <> 'tiffin' AND status IN {PACKED}) AS short_lines,
            sum(lines) FILTER (WHERE kind <> 'tiffin' AND status IN {PACKED}) AS packed_lines
        FROM analytics.fact_orders GROUP BY 1, 2, 3
        """,
        (
            "orders_delivered",
            "boxes_delivered",
            "households_active",
            "gmv_paise",
            "refunds_paise",
            "on_time_orders",
            "first_attempt_orders",
            "dispatched_orders",
            "short_lines",
            "packed_lines",
        ),
    ),
    (
        "c",
        f"""
        SELECT hub_id, delivery_date AS date, data_source,
            count(*) AS cycles_generated,
            count(*) FILTER (WHERE status IN {APPROVED}) AS cycles_approved,
            count(*) FILTER (WHERE status IN {APPROVED} AND auto_approved) AS cycles_auto_approved,
            count(*) FILTER (WHERE status = 'skipped') AS cycles_skipped,
            count(*) FILTER (WHERE approved_before_cutoff) AS cycles_approved_on_time,
            count(*) FILTER (WHERE plan_source = 'ml') AS cycles_ml,
            count(*) FILTER (WHERE plan_source = 'ml' AND status IN {APPROVED}) AS cycles_ml_approved,
            count(*) FILTER (WHERE plan_source = 'fallback') AS cycles_fallback,
            count(*) FILTER (WHERE plan_source = 'fallback' AND status IN {APPROVED})
                AS cycles_fallback_approved,
            sum(edits) FILTER (WHERE status IN {APPROVED}) AS line_edits,
            sum(ran_out_taps) AS ran_out_taps,
            sum(wasted_taps) AS wasted_taps,
            sum(ran_out_taps + wasted_taps + just_right_taps) AS pantry_taps
        FROM analytics.fact_cycles GROUP BY 1, 2, 3
        """,
        (
            "cycles_generated",
            "cycles_approved",
            "cycles_auto_approved",
            "cycles_skipped",
            "cycles_approved_on_time",
            "cycles_ml",
            "cycles_ml_approved",
            "cycles_fallback",
            "cycles_fallback_approved",
            "line_edits",
            "ran_out_taps",
            "wasted_taps",
            "pantry_taps",
        ),
    ),
    (
        "i",
        """
        SELECT hub_id, (created_at AT TIME ZONE 'Asia/Kolkata')::date AS date, data_source,
            count(*) AS issues,
            count(*) FILTER (WHERE status = 'credited' AND resolved_by = 'auto')
                AS issues_auto_credited
        FROM analytics.fact_issues GROUP BY 1, 2, 3
        """,
        ("issues", "issues_auto_credited"),
    ),
    (
        "h",
        """
        SELECT hub_id, date, data_source,
            sum(packed_g) AS packed_g,
            sum(waste_ngo_g) AS waste_ngo_g,
            sum(waste_bin_g) AS waste_bin_g,
            sum(waste_kitchen_g) AS waste_kitchen_g,
            sum(waste_imperfect_g) AS waste_imperfect_g
        FROM analytics.fact_hub_days GROUP BY 1, 2, 3
        """,
        ("packed_g", "waste_ngo_g", "waste_bin_g", "waste_kitchen_g", "waste_imperfect_g"),
    ),
    (
        "r",
        """
        SELECT hub_id, date, data_source,
            count(*) AS routes,
            sum(km_m) AS route_km_m,
            sum(cost_paise) AS route_cost_paise,
            sum(km_m) FILTER (WHERE kind = 'box') AS box_route_km_m,
            sum(cost_paise) FILTER (WHERE kind = 'box') AS box_route_cost_paise,
            sum(orders) FILTER (WHERE kind = 'box') AS box_route_orders
        FROM analytics.fact_routes GROUP BY 1, 2, 3
        """,
        (
            "routes",
            "route_km_m",
            "route_cost_paise",
            "box_route_km_m",
            "box_route_cost_paise",
            "box_route_orders",
        ),
    ),
    (
        "m",
        """
        SELECT hub_id, date, data_source,
            count(*) FILTER (WHERE kind = 'donation_verified') AS donations_verified,
            sum(items) FILTER (WHERE kind = 'donation_verified') AS items_donated,
            sum(kg_tenths) FILTER (WHERE kind = 'donation_verified') AS kg_tenths_donated,
            sum(meals) FILTER (WHERE kind = 'meals_pledged') AS meals_donated,
            count(*) FILTER (WHERE kind = 'referral_converted') AS referrals_converted,
            count(*) FILTER (WHERE kind = 'society_live') AS societies_live
        FROM analytics.fact_community WHERE hub_id IS NOT NULL GROUP BY 1, 2, 3
        """,
        (
            "donations_verified",
            "items_donated",
            "kg_tenths_donated",
            "meals_donated",
            "referrals_converted",
            "societies_live",
        ),
    ),
)


def _daily_hub_kpis() -> str:
    ctes = ",\n".join(f"{name} AS ({body})" for name, body, _ in DAILY)
    keys = " UNION ".join(f"SELECT {', '.join(KEY)} FROM {name}" for name, _, _ in DAILY)
    columns = ",\n    ".join(
        f"coalesce({name}.{column}, 0)::bigint AS {column}"
        for name, _, metrics in DAILY
        for column in metrics
    )
    joins = "\n".join(f"LEFT JOIN {name} USING ({', '.join(KEY)})" for name, _, _ in DAILY)
    return f"""
    WITH {ctes},
    k AS ({keys})
    SELECT k.hub_id, k.date, k.data_source,
        {columns}
    FROM k
    {joins}
    """


WEEKLY_UNIT_ECONOMICS = """
WITH o AS (
    SELECT hub_id, date_trunc('week', delivery_date)::date AS iso_week, data_source,
        count(*) FILTER (WHERE kind = 'box') AS boxes,
        sum(total_paise - fees_paise - refunded_paise - credited_paise)
            FILTER (WHERE kind = 'box') AS box_revenue_paise,
        sum(fees_paise) FILTER (WHERE kind = 'box') AS box_fees_paise,
        sum(total_paise - fees_paise - refunded_paise - credited_paise)
            FILTER (WHERE kind = 'addon') AS addon_revenue_paise,
        count(*) FILTER (WHERE kind = 'tiffin') AS tiffins,
        sum(total_paise - fees_paise - refunded_paise - credited_paise)
            FILTER (WHERE kind = 'tiffin') AS tiffin_revenue_paise
    FROM analytics.fact_orders WHERE status = 'delivered' GROUP BY 1, 2, 3
),
h AS (
    SELECT hub_id, date_trunc('week', date)::date AS iso_week, data_source,
        CASE WHEN count(*) = count(cogs_paise) THEN sum(cogs_paise) END AS cogs_paise
    FROM analytics.fact_hub_days GROUP BY 1, 2, 3
),
r AS (
    SELECT hub_id, date_trunc('week', date)::date AS iso_week, data_source,
        CASE WHEN count(*) = count(cost_paise) THEN sum(cost_paise) END AS box_route_cost_paise,
        sum(orders) AS box_route_orders
    FROM analytics.fact_routes WHERE kind = 'box' GROUP BY 1, 2, 3
),
p AS (
    SELECT hub_id, iso_week, data_source,
        sum(amount_paise) FILTER (WHERE kind = 'captain') AS captain_paise,
        sum(amount_paise) FILTER (WHERE kind = 'packer') AS packer_paise,
        sum(amount_paise) FILTER (WHERE kind = 'rider') AS rider_paise,
        sum(amount_paise) FILTER (WHERE kind = 'kitchen') AS kitchen_paise
    FROM analytics.fact_partner_weeks GROUP BY 1, 2, 3
),
k AS (
    SELECT hub_id, iso_week, data_source FROM o
    UNION SELECT hub_id, iso_week, data_source FROM h
    UNION SELECT hub_id, iso_week, data_source FROM r
    UNION SELECT hub_id, iso_week, data_source FROM p
)
SELECT k.hub_id, k.iso_week, k.data_source,
    coalesce(o.boxes, 0)::bigint AS boxes,
    coalesce(o.box_revenue_paise, 0)::bigint AS box_revenue_paise,
    coalesce(o.box_fees_paise, 0)::bigint AS box_fees_paise,
    coalesce(o.addon_revenue_paise, 0)::bigint AS addon_revenue_paise,
    coalesce(o.tiffins, 0)::bigint AS tiffins,
    coalesce(o.tiffin_revenue_paise, 0)::bigint AS tiffin_revenue_paise,
    h.cogs_paise::bigint AS cogs_paise,
    CASE WHEN r.hub_id IS NULL THEN 0 ELSE r.box_route_cost_paise END::bigint
        AS box_route_cost_paise,
    coalesce(r.box_route_orders, 0)::bigint AS box_route_orders,
    coalesce(p.captain_paise, 0)::bigint AS captain_paise,
    coalesce(p.packer_paise, 0)::bigint AS packer_paise,
    coalesce(p.rider_paise, 0)::bigint AS rider_paise,
    coalesce(p.kitchen_paise, 0)::bigint AS kitchen_paise
FROM k
LEFT JOIN o USING (hub_id, iso_week, data_source)
LEFT JOIN h USING (hub_id, iso_week, data_source)
LEFT JOIN r USING (hub_id, iso_week, data_source)
LEFT JOIN p USING (hub_id, iso_week, data_source)
"""

# Week 1 is the week after the signup week; a household is active in a week with a delivered order.
COHORTS = """
WITH sizes AS (
    SELECT data_source, hub_id, cohort_week, count(*) AS households
    FROM analytics.fact_households GROUP BY 1, 2, 3
),
active AS (
    SELECT h.data_source, h.hub_id, h.cohort_week,
        (date_trunc('week', o.delivery_date)::date - h.cohort_week) / 7 AS week_offset,
        count(DISTINCT h.household_id) AS active
    FROM analytics.fact_households h
    JOIN analytics.fact_orders o ON o.household_id = h.household_id AND o.status = 'delivered'
    GROUP BY 1, 2, 3, 4
)
SELECT s.data_source, s.hub_id, s.cohort_week, w.week_offset::smallint AS week_offset,
    s.households::bigint AS households, coalesce(a.active, 0)::bigint AS active
FROM sizes s
CROSS JOIN generate_series(1, 26) AS w(week_offset)
LEFT JOIN active a
    ON a.data_source = s.data_source
    AND a.hub_id IS NOT DISTINCT FROM s.hub_id
    AND a.cohort_week = s.cohort_week
    AND a.week_offset = w.week_offset
"""

# Per paid partner, then the distribution; percentiles and Gini only from 5 paid partners (k = 5).
# Gini = 2 Σ i·x_i / (n Σ x) - (n + 1) / n over the ascending earnings, as M8's matching computes.
PARTNER_EARNINGS_WEEKLY = """
WITH per AS (
    SELECT hub_id, kind, iso_week, data_source, partner_id, sum(amount_paise) AS paise
    FROM analytics.fact_partner_weeks
    GROUP BY 1, 2, 3, 4, 5
    HAVING sum(amount_paise) > 0
),
ranked AS (
    SELECT per.*, row_number() OVER (
        PARTITION BY hub_id, kind, iso_week, data_source ORDER BY paise, partner_id
    ) AS i
    FROM per
)
SELECT hub_id, kind, iso_week, data_source,
    count(*)::bigint AS partners_paid,
    sum(paise)::bigint AS total_paise,
    CASE WHEN count(*) >= 5 THEN
        round((percentile_cont(0.1) WITHIN GROUP (ORDER BY paise))::numeric)::bigint END
        AS p10_paise,
    CASE WHEN count(*) >= 5 THEN
        round((percentile_cont(0.5) WITHIN GROUP (ORDER BY paise))::numeric)::bigint END
        AS median_paise,
    CASE WHEN count(*) >= 5 THEN
        round((percentile_cont(0.9) WITHIN GROUP (ORDER BY paise))::numeric)::bigint END
        AS p90_paise,
    CASE WHEN count(*) >= 5 THEN
        round(10000 * (2.0 * sum(i * paise) / (count(*) * sum(paise))
            - (count(*) + 1.0) / count(*)))::integer END
        AS gini_bp
FROM ranked
GROUP BY 1, 2, 3, 4
"""

IMPACT_COLUMNS = """
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

# Hub totals (ngo_id null), then one row per NGO for what it received.
IMPACT_MONTHLY = f"""
SELECT hub_id, NULL::uuid AS ngo_id, date_trunc('month', date)::date AS month, data_source,
    {IMPACT_COLUMNS}
FROM analytics.fact_community GROUP BY 1, 2, 3, 4
UNION ALL
SELECT hub_id, ngo_id, date_trunc('month', date)::date AS month, data_source,
    {IMPACT_COLUMNS}
FROM analytics.fact_community WHERE ngo_id IS NOT NULL GROUP BY 1, 2, 3, 4
"""

# view -> (its SELECT, its grain, whether a grain column may be null)
VIEWS: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "mv_daily_hub_kpis": (_daily_hub_kpis(), KEY, False),
    "mv_weekly_unit_economics": (
        WEEKLY_UNIT_ECONOMICS,
        ("hub_id", "iso_week", "data_source"),
        False,
    ),
    "mv_cohorts": (COHORTS, ("data_source", "hub_id", "cohort_week", "week_offset"), True),
    "mv_partner_earnings_weekly": (
        PARTNER_EARNINGS_WEEKLY,
        ("hub_id", "kind", "iso_week", "data_source"),
        False,
    ),
    "mv_impact_monthly": (IMPACT_MONTHLY, ("hub_id", "ngo_id", "month", "data_source"), True),
}


def _create_views() -> None:
    for name, (select, grain, nullable) in VIEWS.items():
        op.execute(f"CREATE MATERIALIZED VIEW analytics.{name} AS {select}")
        nulls = " NULLS NOT DISTINCT" if nullable else ""
        op.execute(
            f"CREATE UNIQUE INDEX uq_{name}_grain ON analytics.{name} ({', '.join(grain)}){nulls}"
        )


def _defaults() -> None:
    table = sa.table(
        "assumptions",
        sa.column("key", sa.String),
        sa.column("value", sa.BigInteger),
        schema="analytics",
    )
    op.bulk_insert(table, [{"key": key, "value": value} for key, value in DEFAULTS.items()])


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "assumption_changes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=True),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("from_value", sa.BigInteger(), nullable=True),
        sa.Column("to_value", sa.BigInteger(), nullable=False),
        sa.Column("by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "key IN ('aov_paise', 'margin_bp', 'delivery_paise', 'packing_paise', 'captain_paise', 'payment_fee_bp', 'cac_paise')",
            name=op.f("ck_assumption_changes_key"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assumption_changes")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_assumption_changes_hub_id"),
        "assumption_changes",
        ["hub_id", "at"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "assumptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=True),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "key IN ('aov_paise', 'margin_bp', 'delivery_paise', 'packing_paise', 'captain_paise', 'payment_fee_bp', 'cac_paise')",
            name=op.f("ck_assumptions_key"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assumptions")),
        sa.UniqueConstraint(
            "hub_id",
            "key",
            name=op.f("uq_assumptions_hub_id_key"),
            postgresql_nulls_not_distinct=True,
        ),
        schema="analytics",
    )
    op.create_table(
        "fact_assignment_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("meal", sa.String(length=8), nullable=False),
        sa.Column("portions", sa.Integer(), nullable=False),
        sa.Column("gini_bp", sa.Integer(), nullable=True),
        sa.Column("km_per_portion_m", sa.Integer(), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_assignment_runs_data_source")
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_fact_assignment_runs")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_assignment_runs_date"),
        "fact_assignment_runs",
        ["date", "hub_id"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "fact_community",
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=True),
        sa.Column("society_id", sa.Uuid(), nullable=True),
        sa.Column("ngo_id", sa.Uuid(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("items", sa.Integer(), nullable=False),
        sa.Column("kg_tenths", sa.Integer(), nullable=False),
        sa.Column("meals", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("people_reached", sa.Integer(), nullable=True),
        sa.Column("household_id", sa.Uuid(), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_community_data_source")
        ),
        sa.CheckConstraint(
            "kind IN ('donation_verified', 'referral_converted', 'handoff_items', 'handoff_food', 'handoff_meals', 'meals_pledged', 'society_live')",
            name=op.f("ck_fact_community_kind"),
        ),
        sa.PrimaryKeyConstraint("source", "source_id", name=op.f("pk_fact_community")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_community_date"),
        "fact_community",
        ["date", "kind"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "fact_cycles",
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("auto_approved", sa.Boolean(), nullable=False),
        sa.Column("approved_before_cutoff", sa.Boolean(), nullable=False),
        sa.Column("edits", sa.Integer(), nullable=False),
        sa.Column("plan_source", sa.String(length=10), nullable=False),
        sa.Column("ran_out_taps", sa.Integer(), nullable=False),
        sa.Column("wasted_taps", sa.Integer(), nullable=False),
        sa.Column("just_right_taps", sa.Integer(), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_cycles_data_source")
        ),
        sa.PrimaryKeyConstraint("cycle_id", name=op.f("pk_fact_cycles")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_cycles_delivery_date"),
        "fact_cycles",
        ["delivery_date", "hub_id"],
        unique=False,
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_cycles_household_id"),
        "fact_cycles",
        ["household_id"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "fact_households",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=True),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("created_on", sa.Date(), nullable=False),
        sa.Column("cohort_week", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_households_data_source")
        ),
        sa.PrimaryKeyConstraint("household_id", name=op.f("pk_fact_households")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_households_cohort_week"),
        "fact_households",
        ["cohort_week"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "fact_hub_days",
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("packed_g", sa.BigInteger(), nullable=False),
        sa.Column("waste_ngo_g", sa.BigInteger(), nullable=False),
        sa.Column("waste_bin_g", sa.BigInteger(), nullable=False),
        sa.Column("waste_kitchen_g", sa.BigInteger(), nullable=False),
        sa.Column("waste_imperfect_g", sa.BigInteger(), nullable=False),
        sa.Column("cogs_paise", sa.BigInteger(), nullable=True),
        sa.Column("surplus_offered_g", sa.BigInteger(), nullable=False),
        sa.Column("surplus_taken_g", sa.BigInteger(), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_hub_days_data_source")
        ),
        sa.PrimaryKeyConstraint("hub_id", "date", name=op.f("pk_fact_hub_days")),
        schema="analytics",
    )
    op.create_table(
        "fact_issues",
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("credit_paise", sa.BigInteger(), nullable=False),
        sa.Column("resolved_by", sa.String(length=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_issues_data_source")
        ),
        sa.PrimaryKeyConstraint("issue_id", name=op.f("pk_fact_issues")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_issues_order_id"),
        "fact_issues",
        ["order_id"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "fact_orders",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("fees_paise", sa.BigInteger(), nullable=False),
        sa.Column("refunded_paise", sa.BigInteger(), nullable=False),
        sa.Column("credited_paise", sa.BigInteger(), nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
        sa.Column("short_lines", sa.Integer(), nullable=False),
        sa.Column("on_time", sa.Boolean(), nullable=True),
        sa.Column("first_attempt", sa.Boolean(), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_orders_data_source")
        ),
        sa.CheckConstraint(
            "total_paise >= 0 AND fees_paise >= 0 AND refunded_paise >= 0 AND credited_paise >= 0",
            name=op.f("ck_fact_orders_money"),
        ),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_fact_orders")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_orders_delivery_date"),
        "fact_orders",
        ["delivery_date", "hub_id"],
        unique=False,
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_orders_household_id"),
        "fact_orders",
        ["household_id"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "fact_partner_weeks",
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("iso_week", sa.Date(), nullable=False),
        sa.Column("basis", sa.String(length=20), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_partner_weeks_data_source")
        ),
        sa.PrimaryKeyConstraint(
            "partner_id", "iso_week", "basis", name=op.f("pk_fact_partner_weeks")
        ),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_partner_weeks_iso_week"),
        "fact_partner_weeks",
        ["iso_week", "hub_id"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "fact_routes",
        sa.Column("route_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("km_m", sa.Integer(), nullable=False),
        sa.Column("cost_paise", sa.BigInteger(), nullable=True),
        sa.Column("orders", sa.Integer(), nullable=False),
        sa.Column("stops", sa.Integer(), nullable=False),
        sa.Column("on_time_stops", sa.Integer(), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_fact_routes_data_source")
        ),
        sa.PrimaryKeyConstraint("route_id", name=op.f("pk_fact_routes")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_fact_routes_date"),
        "fact_routes",
        ["date", "hub_id"],
        unique=False,
        schema="analytics",
    )
    op.create_table(
        "refresh_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("job", sa.String(length=32), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_from", sa.Date(), nullable=False),
        sa.Column("window_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=8), server_default="running", nullable=False),
        sa.Column(
            "rows",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "job IN ('refresh_analytics', 'refresh_analytics_nightly', 'rebuild_analytics')",
            name=op.f("ck_refresh_runs_job"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'failed')", name=op.f("ck_refresh_runs_status")
        ),
        sa.CheckConstraint("window_to >= window_from", name=op.f("ck_refresh_runs_window")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_runs")),
        schema="analytics",
    )
    op.create_index(
        op.f("ix_analytics_refresh_runs_job"),
        "refresh_runs",
        ["job", "status", "finished_at"],
        unique=False,
        schema="analytics",
    )
    # ### end Alembic commands ###
    _defaults()
    _create_views()


def downgrade() -> None:
    for name in reversed(VIEWS):
        op.execute(f"DROP MATERIALIZED VIEW analytics.{name}")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(
        op.f("ix_analytics_refresh_runs_job"), table_name="refresh_runs", schema="analytics"
    )
    op.drop_table("refresh_runs", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_routes_date"), table_name="fact_routes", schema="analytics"
    )
    op.drop_table("fact_routes", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_partner_weeks_iso_week"),
        table_name="fact_partner_weeks",
        schema="analytics",
    )
    op.drop_table("fact_partner_weeks", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_orders_household_id"), table_name="fact_orders", schema="analytics"
    )
    op.drop_index(
        op.f("ix_analytics_fact_orders_delivery_date"), table_name="fact_orders", schema="analytics"
    )
    op.drop_table("fact_orders", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_issues_order_id"), table_name="fact_issues", schema="analytics"
    )
    op.drop_table("fact_issues", schema="analytics")
    op.drop_table("fact_hub_days", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_households_cohort_week"),
        table_name="fact_households",
        schema="analytics",
    )
    op.drop_table("fact_households", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_cycles_household_id"), table_name="fact_cycles", schema="analytics"
    )
    op.drop_index(
        op.f("ix_analytics_fact_cycles_delivery_date"), table_name="fact_cycles", schema="analytics"
    )
    op.drop_table("fact_cycles", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_community_date"), table_name="fact_community", schema="analytics"
    )
    op.drop_table("fact_community", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_fact_assignment_runs_date"),
        table_name="fact_assignment_runs",
        schema="analytics",
    )
    op.drop_table("fact_assignment_runs", schema="analytics")
    op.drop_table("assumptions", schema="analytics")
    op.drop_index(
        op.f("ix_analytics_assumption_changes_hub_id"),
        table_name="assumption_changes",
        schema="analytics",
    )
    op.drop_table("assumption_changes", schema="analytics")
    # ### end Alembic commands ###
