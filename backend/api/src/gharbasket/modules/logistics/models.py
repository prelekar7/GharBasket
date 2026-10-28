"""Tables of M7 Logistics. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Vehicles (spec §3.1): each hub's fleet, deactivated instead of deleted. Route plans (§3.2): one
solve for a hub, a delivery date and a kind, holding routes (one trip of one vehicle) and their
stops (a society and slot). Foreign keys stay inside `logistics`; hub, society, slot, order and
user ids are plain UUIDs (ADR-0026 §5). Rider execution and the handover (§3.3, §3.4) write
routes, stops, handovers, deliveries and field events; return-leg pickups (§3.6) are `pickups`.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ColumnElement,
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
    or_,
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
# nearest: a tiffin plan, kitchens then societies by nearest neighbour in M7 (§3.7)
PLAN_SOURCES = ("ml", "ml_fallback", "fallback_previous", "fallback_nearest", "nearest")
ROUTE_STATUSES = ("draft", "published", "in_progress", "completed", "cancelled")
STARTED = ("in_progress", "completed")
STOP_KINDS = ("drop", "bank", "kitchen")
STOP_STATUSES = ("pending", "arrived", "handed_over", "skipped")
SKIP_REASONS = ("captain_absent", "no_access", "vehicle_issue", "other")
FIELD_ACTIONS = (
    "start",
    "arrive",
    "skip",
    "handover",
    "proof",
    "undelivered",
    "hold",
    "resolve",
    "complete",
    "pickup",
    "rider_mode",
    "collect",
)


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
    # Orders of its stops still locked or picking when it started with leave_behind (§3.3).
    left_behind: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class RouteStop(Base):
    """A society and slot on a route (`drop`), or a kitchen a tiffin route collects from
    (`kitchen`, §3.7); `bank` stops come with donations. Window and ETA are IST clock times;
    crates and bags are planned until the route starts."""

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
    # A kitchen stop's M8 assignment and kitchen (§3.7); plain ids, no cross-schema FK.
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    portions: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))  # tiffins
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
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StopOrder(Base):
    """A tiffin on a stop of a tiffin route (§3.7): each tiffin is on two rows of one route, its
    kitchen stop and its drop. No personal data."""

    __tablename__ = "stop_orders"
    __table_args__ = (
        UniqueConstraint("stop_id", "order_id"),
        Index("ix_stop_orders_order_id", "order_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    stop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.route_stops.id", ondelete="CASCADE")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M4's tiffin order
    short_code: Mapped[str] = mapped_column(String(8))  # M8's label, "T-07"
    portions: Mapped[int] = mapped_column(SmallInteger)


HANDOVER_ACTORS = ("captain", "rider", "hub")
HANDOVER_PROOFS = ("code", "photo", "rider_mode")


class Handover(Base):
    """The bags of a stop handed to its society's captain (or the rider, in rider mode), once per
    stop (spec §3.4). Counts are what the captain counted against what the van carried."""

    __tablename__ = "handovers"
    __table_args__ = (
        CheckConstraint(_one_of("by_actor", HANDOVER_ACTORS), name="by_actor"),
        CheckConstraint(_one_of("proof", HANDOVER_PROOFS), name="proof"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    stop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.route_stops.id", ondelete="CASCADE"), unique=True
    )
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    by_actor: Mapped[str] = mapped_column(String(8))
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # null once the user is erased
    partner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # the captain's M8 partner
    crates_expected: Mapped[int] = mapped_column(SmallInteger)
    crates_counted: Mapped[int] = mapped_column(SmallInteger)
    bags_expected: Mapped[int] = mapped_column(SmallInteger)
    bags_counted: Mapped[int] = mapped_column(SmallInteger)
    mismatch: Mapped[bool] = mapped_column(Boolean)
    proof: Mapped[str] = mapped_column(String(12))
    photo_key: Mapped[str | None] = mapped_column(String(200))
    distance_m: Mapped[int | None] = mapped_column(Integer)  # the rider's, from the last ping
    note: Mapped[str | None] = mapped_column(String(300))
    client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldEvent(Base):
    """What a rider, captain or hub manager did on a route, for audit (spec §4): `client_at` is
    when the tap happened on the phone, `at` when the server took it (the server's time decides)."""

    __tablename__ = "field_events"
    __table_args__ = (
        CheckConstraint(_one_of("action", FIELD_ACTIONS), name="action"),
        Index("ix_field_events_route", "route_id", "at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    route_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routes.id", ondelete="CASCADE")
    )
    stop_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.route_stops.id", ondelete="CASCADE")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(12))
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # null once the user is erased
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


PICKUP_KINDS = ("empties", "donation", "bag")
PICKUP_STATUSES = ("expected", "collected", "not_ready")


class Pickup(Base):
    """What the van takes back at a drop stop (spec §3.6), made when its route starts: the
    society's open empties (one row per stop, `ref_id` null), each donation M9 scheduled for it
    that day, and each bag of a box refunded on its second attempt while the captain held it.
    `qty` is what was expected, `collected_qty` what the rider reported."""

    __tablename__ = "pickups"
    __table_args__ = (
        CheckConstraint(_one_of("kind", PICKUP_KINDS), name="kind"),
        CheckConstraint(_one_of("status", PICKUP_STATUSES), name="status"),
        CheckConstraint("(kind = 'empties') = (ref_id IS NULL)", name="ref_id"),
        CheckConstraint("qty >= 0 AND (collected_qty IS NULL OR collected_qty >= 0)", name="qty"),
        UniqueConstraint("stop_id", "kind", "ref_id", postgresql_nulls_not_distinct=True),
        Index("ix_pickups_hub_date", "hub_id", "delivery_date"),
        Index("ix_pickups_society_kind_status", "society_id", "kind", "status"),
        Index("ix_pickups_ref", "ref_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    stop_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.route_stops.id", ondelete="CASCADE")
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routes.id", ondelete="CASCADE")
    )
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    delivery_date: Mapped[date] = mapped_column(Date)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(8))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M9's donation or M4's order
    # The donation's or the bag's household, for the flat label only; nulled by M1's erasure.
    household_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    items: Mapped[str | None] = mapped_column(String(300))  # M9's summary of a donation
    qty: Mapped[int] = mapped_column(SmallInteger)
    collected_qty: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(12), server_default="expected")
    by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GpsPing(Base):
    """One accepted position of a rider on an in-progress route (spec §3.3), kept 90 days.
    Partitioned by month on `at` (the partitions are made as pings arrive, `tracking.py`); a
    replayed ping inserts nothing (the key is the rider and the time)."""

    __tablename__ = "gps_pings"
    __table_args__ = (
        Index("ix_gps_pings_at", "at", postgresql_using="brin"),
        # A route's newest ping: live tracking and the handover's distance read it
        Index("ix_gps_pings_route_at", "route_id", text("at DESC")),
        {"schema": SCHEMA, "postgresql_partition_by": "RANGE (at)"},
    )

    rider_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    route_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # no FK: a partitioned log
    location: Mapped[Any] = mapped_column(Geography("POINT", srid=4326, spatial_index=False))
    accuracy_m: Mapped[int] = mapped_column(SmallInteger)
    speed_mps: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[int | None] = mapped_column(SmallInteger)


DELIVERY_STATUSES = (
    "scheduled",
    "on_route",
    "with_captain",
    "held",
    "delivered",
    "undelivered",
    "redelivery",
    "refunded",
    "triage",
)
DELIVERY_REASONS = (
    "not_home",
    "refused",
    "wrong_address",
    "damaged",
    "missing",
    "other",
    "returned_to_hub",
    "not_confirmed",
    "left_behind",  # not packed when its route started; the day-end sweep sends it to triage
)


class Delivery(Base):
    """One attempt at an order's door (spec §3.4): attempt 1 when its route starts, attempt 2 for
    the one free redelivery. `delivery_code` is the household's code for a rider's otp proof."""

    __tablename__ = "deliveries"
    __table_args__ = (
        CheckConstraint("attempt IN (1, 2)", name="attempt"),
        CheckConstraint(_one_of("status", DELIVERY_STATUSES), name="status"),
        CheckConstraint(f"reason IS NULL OR {_one_of('reason', DELIVERY_REASONS)}", name="reason"),
        CheckConstraint("proof IS NULL OR proof IN ('scan', 'otp', 'photo')", name="proof"),
        CheckConstraint(
            "by_actor IS NULL OR by_actor IN ('captain', 'rider', 'hub', 'system')",
            name="by_actor",
        ),
        UniqueConstraint("order_id", "attempt"),
        Index("ix_deliveries_hub_date", "hub_id", "delivery_date", "status"),
        # The evening jobs: delivery_date <= day and status in (…), for every hub at once
        Index("ix_deliveries_date_status", "delivery_date", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M4's order
    attempt: Mapped[int] = mapped_column(SmallInteger)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    order_kind: Mapped[str] = mapped_column(String(8))
    delivery_date: Mapped[date] = mapped_column(Date)  # the attempt's day
    slot_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    stop_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.route_stops.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(String(16))
    hold_until: Mapped[time | None] = mapped_column(Time)  # IST
    proof: Mapped[str | None] = mapped_column(String(8))
    photo_key: Mapped[str | None] = mapped_column(String(200))
    # The attempt ended at the society with the bag in the captain's hands (held and not collected,
    # refused, wrong address, never scanned…), so its redelivery comes from her, not from the van:
    # the van carries nothing for it, but her society needs the stop (§3.4).
    kept_by_captain: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Attempt 1 booked again for a later day because the hub left the bag behind: it rides like a
    # redelivery, but it is no customer miss, so a real miss on it still gets the free redelivery.
    hub_ride: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    short_code: Mapped[str | None] = mapped_column(String(8))  # a tiffin's label (§3.7)
    delivery_code: Mapped[str] = mapped_column(String(8))
    code_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    by_actor: Mapped[str | None] = mapped_column(String(8))
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    refund_paise: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(300))
    client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def booked() -> ColumnElement[bool]:
    """An attempt booked for a later day: the free redelivery (attempt 2) or the ride of a bag the
    hub's route left behind (`hub_ride`, attempt 1 booked again)."""
    return or_(Delivery.attempt == 2, Delivery.hub_ride.is_(True))
