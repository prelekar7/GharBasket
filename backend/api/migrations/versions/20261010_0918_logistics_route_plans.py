"""logistics: route plans, routes and stops (M7 §3.2, §4; ADR-0026, ADR-0027)

A plan is one solve for a hub, a delivery date and a kind; it holds routes (one trip of one
vehicle) and their stops (a society and slot). At most one plan per (hub, date, kind) is queued
or solving and one is published (partial unique indexes). The route and stop columns for rider
execution and the handover (§3.3, §3.4) are created now, empty until their PRs. Foreign keys stay
inside `logistics`; hub, society, slot and user ids are plain UUIDs (ADR-0026 §5).

Revision ID: a44a8c013f2a
Revises: 46ba92e3b0c3
Create Date: 2026-10-10 09:18:11.806428+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a44a8c013f2a"
down_revision: str | Sequence[str] | None = "46ba92e3b0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "route_plans",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="queued", nullable=False),
        sa.Column("trigger", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("distance_source", sa.String(length=10), nullable=True),
        sa.Column("model", sa.String(length=40), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("unserved", sa.ARRAY(sa.String(length=80)), server_default="{}", nullable=False),
        sa.Column("prediction_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "distance_source IS NULL OR distance_source IN ('osrm', 'haversine')",
            name=op.f("ck_route_plans_distance_source"),
        ),
        sa.CheckConstraint("kind IN ('box', 'tiffin')", name=op.f("ck_route_plans_kind")),
        sa.CheckConstraint(
            "source IS NULL OR source IN "
            "('ml', 'ml_fallback', 'fallback_previous', 'fallback_nearest')",
            name=op.f("ck_route_plans_source"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'solving', 'solved', 'published', 'failed', 'superseded')",
            name=op.f("ck_route_plans_status"),
        ),
        sa.CheckConstraint("trigger IN ('job', 'manual')", name=op.f("ck_route_plans_trigger")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_route_plans")),
        schema="logistics",
    )
    op.create_index(
        "ix_route_plans_hub_date",
        "route_plans",
        ["hub_id", "delivery_date", "kind"],
        unique=False,
        schema="logistics",
    )
    op.create_index(
        "uq_route_plans_in_flight",
        "route_plans",
        ["hub_id", "delivery_date", "kind"],
        unique=True,
        schema="logistics",
        postgresql_where=sa.text("status IN ('queued', 'solving')"),
    )
    op.create_index(
        "uq_route_plans_published",
        "route_plans",
        ["hub_id", "delivery_date", "kind"],
        unique=True,
        schema="logistics",
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "routes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("trip", sa.SmallInteger(), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("rider_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=12), server_default="draft", nullable=False),
        sa.Column("depart_time", sa.Time(), nullable=False),
        sa.Column("return_by", sa.Time(), nullable=False),
        sa.Column("planned_km", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("empties_returned", sa.SmallInteger(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("kind IN ('box', 'tiffin')", name=op.f("ck_routes_kind")),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'in_progress', 'completed', 'cancelled')",
            name=op.f("ck_routes_status"),
        ),
        sa.CheckConstraint("trip >= 1", name=op.f("ck_routes_trip")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_routes_version")),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["logistics.route_plans.id"],
            name=op.f("fk_routes_plan_id_route_plans"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["logistics.vehicles.id"],
            name=op.f("fk_routes_vehicle_id_vehicles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routes")),
        sa.UniqueConstraint(
            "plan_id", "vehicle_id", "trip", name=op.f("uq_routes_plan_id_vehicle_id_trip")
        ),
        schema="logistics",
    )
    op.create_index(
        "ix_routes_hub_date",
        "routes",
        ["hub_id", "delivery_date", "status"],
        unique=False,
        schema="logistics",
    )
    op.create_index(
        "ix_routes_rider_date",
        "routes",
        ["rider_user_id", "delivery_date"],
        unique=False,
        schema="logistics",
    )
    op.create_table(
        "route_stops",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("route_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.String(length=8), server_default="drop", nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=True),
        sa.Column("slot_id", sa.Uuid(), nullable=True),
        sa.Column("window_start", sa.Time(), nullable=True),
        sa.Column("window_end", sa.Time(), nullable=True),
        sa.Column("eta", sa.Time(), nullable=False),
        sa.Column("late_s", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("delay_s", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("rider_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("handover_code", sa.String(length=8), nullable=True),
        sa.Column("code_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("orders", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("crates", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("bags", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("kg_tenths", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "empties_expected", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skip_reason", sa.String(length=16), nullable=True),
        sa.CheckConstraint("kind IN ('drop', 'bank', 'kitchen')", name=op.f("ck_route_stops_kind")),
        sa.CheckConstraint(
            "skip_reason IS NULL OR skip_reason IN "
            "('captain_absent', 'no_access', 'vehicle_issue', 'other')",
            name=op.f("ck_route_stops_skip_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'arrived', 'handed_over', 'skipped')",
            name=op.f("ck_route_stops_status"),
        ),
        sa.CheckConstraint("seq >= 1", name=op.f("ck_route_stops_seq")),
        sa.ForeignKeyConstraint(
            ["route_id"],
            ["logistics.routes.id"],
            name=op.f("fk_route_stops_route_id_routes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_route_stops")),
        sa.UniqueConstraint("route_id", "seq", name=op.f("uq_route_stops_route_id_seq")),
        schema="logistics",
    )
    op.create_index(
        "ix_route_stops_society", "route_stops", ["society_id"], unique=False, schema="logistics"
    )


def downgrade() -> None:
    op.drop_index("ix_route_stops_society", table_name="route_stops", schema="logistics")
    op.drop_table("route_stops", schema="logistics")
    op.drop_index("ix_routes_rider_date", table_name="routes", schema="logistics")
    op.drop_index("ix_routes_hub_date", table_name="routes", schema="logistics")
    op.drop_table("routes", schema="logistics")
    op.drop_index(
        "uq_route_plans_published",
        table_name="route_plans",
        schema="logistics",
        postgresql_where=sa.text("status = 'published'"),
    )
    op.drop_index(
        "uq_route_plans_in_flight",
        table_name="route_plans",
        schema="logistics",
        postgresql_where=sa.text("status IN ('queued', 'solving')"),
    )
    op.drop_index("ix_route_plans_hub_date", table_name="route_plans", schema="logistics")
    op.drop_table("route_plans", schema="logistics")
