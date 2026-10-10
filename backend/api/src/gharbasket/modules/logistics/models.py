"""Tables of M7 Logistics. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Vehicles (spec §3.1): each hub's fleet, deactivated instead of deleted. Route plans (§3.2): one
solve for a hub, a delivery date and a kind, holding routes (one trip of one vehicle) and their
stops (a society and slot). Foreign keys stay inside `logistics`; hub, society, slot, order and
user ids are plain UUIDs (ADR-0026 §5). The route and stop columns for rider execution and the
handover (§3.3, §3.4) are here already; their endpoints follow in later PRs.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "logistics"

VEHICLE_KINDS = ("cargo_3w", "two_wheeler")
PLAN_KINDS = ("box", "tiffin")
PLAN_STATUSES = ("queued", "solving", "solved", "published", "failed", "superseded")
IN_FLIGHT = ("queued", "solving")
PLAN_TRIGGERS = ("job", "manual")
PLAN_SOURCES = ("ml", "fallback_previous", "fallback_nearest")
ROUTE_STATUSES = ("draft", "published", "in_progress", "completed", "cancelled")
STARTED = ("in_progress", "completed")
STOP_KINDS = ("drop", "bank", "kitchen")
STOP_STATUSES = ("pending", "arrived", "handed_over", "skipped")
SKIP_REASONS = ("captain_absent", "no_access", "vehicle_issue", "other")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Vehicle(Base):
    """A van or two-wheeler of one hub: boxes and add-ons ride cargo_3w, tiffins two_wheeler
    (ADR-0026 §6). A change applies to the next solve, never to a route already published."""

    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint(_one_of("kind", VEHICLE_KINDS), name="kind"),
        CheckConstraint("capacity_kg BETWEEN 10 AND 2000", name="capacity_kg"),
        CheckConstraint("capacity_crates BETWEEN 0 AND 100", name="capacity_crates"),
        CheckConstraint("capacity_tiffins BETWEEN 0 AND 100", name="capacity_tiffins"),
        CheckConstraint("version >= 1", name="version"),
        Index("uq_vehicles_hub_label", "hub_id", text("lower(label)"), unique=True),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M6's hub; no cross-schema FK
    label: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(16))
    registration: Mapped[str | None] = mapped_column(String(16))
    capacity_kg: Mapped[int] = mapped_column(SmallInteger)
    capacity_crates: Mapped[int] = mapped_column(SmallInteger)
    capacity_tiffins: Mapped[int] = mapped_column(SmallInteger)
    # identity.users.id of a rider of this hub; set on new routes.
    default_rider_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoutePlan(Base):
    """One solve for a hub, a delivery date and a kind (spec §3.2). At most one plan in flight
    and one published per (hub, date, kind)."""

    __tablename__ = "route_plans"
    __table_args__ = (
        CheckConstraint(_one_of("kind", PLAN_KINDS), name="kind"),
        CheckConstraint(_one_of("status", PLAN_STATUSES), name="status"),
        CheckConstraint(_one_of("trigger", PLAN_TRIGGERS), name="trigger"),
        CheckConstraint(f"source IS NULL OR {_one_of('source', PLAN_SOURCES)}", name="source"),
        CheckConstraint(
            "distance_source IS NULL OR distance_source IN ('osrm', 'haversine')",
            name="distance_source",
        ),
        Index("ix_route_plans_hub_date", "hub_id", "delivery_date", "kind"),
        Index(
            "uq_route_plans_published",
            "hub_id",
            "delivery_date",
            "kind",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "uq_route_plans_in_flight",
            "hub_id",
            "delivery_date",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('queued', 'solving')"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    delivery_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12), server_default="queued")
    trigger: Mapped[str] = mapped_column(String(8))
    source: Mapped[str | None] = mapped_column(String(20))
    distance_source: Mapped[str | None] = mapped_column(String(10))
    model: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(500))
    # Stops with orders that are on no route (no location, or the solver left them out): such a
    # plan can never be published (logistics.plan_incomplete).
    unserved: Mapped[list[str]] = mapped_column(ARRAY(String(80)), server_default="{}")
    prediction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # intel.predictions
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    published_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Route(Base):
    """One trip of one vehicle; a van's second trip has `trip` 2. Draft until its plan is
    published."""

    __tablename__ = "routes"
    __table_args__ = (
        CheckConstraint(_one_of("kind", PLAN_KINDS), name="kind"),
        CheckConstraint(_one_of("status", ROUTE_STATUSES), name="status"),
        CheckConstraint("trip >= 1", name="trip"),
        CheckConstraint("version >= 1", name="version"),
        UniqueConstraint("plan_id", "vehicle_id", "trip"),
        Index("ix_routes_hub_date", "hub_id", "delivery_date", "status"),
        Index("ix_routes_rider_date", "rider_user_id", "delivery_date"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.route_plans.id", ondelete="CASCADE")
    )
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    delivery_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(8))
    trip: Mapped[int] = mapped_column(SmallInteger)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vehicles.id", ondelete="RESTRICT")
    )
    rider_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(12), server_default="draft")
    depart_time: Mapped[time] = mapped_column(Time)  # IST
    return_by: Mapped[time] = mapped_column(Time)  # IST
    planned_km: Mapped[float] = mapped_column(Float)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    empties_returned: Mapped[int | None] = mapped_column(SmallInteger)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class RouteStop(Base):
    """A society and slot on a route (`drop`); `bank` and `kitchen` stops come with §3.6, §3.7.
    Window and ETA are IST clock times; crates and bags are planned until the route starts."""

    __tablename__ = "route_stops"
    __table_args__ = (
        CheckConstraint(_one_of("kind", STOP_KINDS), name="kind"),
        CheckConstraint(_one_of("status", STOP_STATUSES), name="status"),
        CheckConstraint(
            f"skip_reason IS NULL OR {_one_of('skip_reason', SKIP_REASONS)}", name="skip_reason"
        ),
        CheckConstraint("seq >= 1", name="seq"),
        UniqueConstraint("route_id", "seq"),
        Index("ix_route_stops_society", "society_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    route_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routes.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str] = mapped_column(String(8), server_default="drop")
    society_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    slot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    window_start: Mapped[time | None] = mapped_column(Time)
    window_end: Mapped[time | None] = mapped_column(Time)
    eta: Mapped[time] = mapped_column(Time)
    late_s: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    delay_s: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(12), server_default="pending")
    rider_mode: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    handover_code: Mapped[str | None] = mapped_column(String(8))
    code_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    orders: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    crates: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    bags: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    kg_tenths: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    empties_expected: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skip_reason: Mapped[str | None] = mapped_column(String(16))
