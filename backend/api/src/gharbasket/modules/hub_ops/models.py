"""Tables of M6 Hub Operations. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Hubs (spec §3.1): hubs with their service area, and the vendors each hub buys from. Packing
(§3.2): one task per order to pack, its lines, and what packers did. Hub ids in other schemas, and
order, slot, society, product and user ids here, are plain UUIDs without a foreign key
(ADR-0026 §5). Procurement, receiving and waste follow in later PRs.
"""

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geography, WKBElement, WKTElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "hub_ops"

HUB_STATUSES = ("active", "paused")
VENDOR_KINDS = ("apmc_trader", "fpo", "farmer", "wholesaler")
TASK_STATUSES = ("queued", "picking", "packed")
PACKING_ACTIONS = ("start", "pick", "shortage", "complete", "release")
# The hub's day (IST), in the order the clock must follow; display and job planning only.
TIMING_KEYS = ("receiving", "packing", "dispatch", "surplus")
DEFAULT_TIMINGS = {
    "receiving": "04:00",
    "packing": "05:00",
    "dispatch": "06:30",
    "surplus": "12:30",
}


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Hub(Base):
    __tablename__ = "hubs"
    __table_args__ = (
        CheckConstraint(_one_of("status", HUB_STATUSES), name="status"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        CheckConstraint("procurement_buffer_pct BETWEEN 0 AND 50", name="procurement_buffer_pct"),
        CheckConstraint("jsonb_typeof(timings) = 'object'", name="timings"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_hubs_service_area", "service_area", postgresql_using="gist"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # Never changes: other modules and URLs may show it.
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    address: Mapped[str] = mapped_column(String(300))
    location: Mapped[WKBElement | WKTElement] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False)
    )
    # Null until an admin draws it. Societies outside it are allowed (the console warns).
    service_area: Mapped[WKBElement | WKTElement | None] = mapped_column(
        Geography("POLYGON", srid=4326, spatial_index=False)
    )
    status: Mapped[str] = mapped_column(String(8), server_default="active")
    # {"receiving": "04:00", "packing": "05:00", "dispatch": "06:30", "surplus": "12:30"}
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    procurement_buffer_pct: Mapped[int] = mapped_column(SmallInteger, server_default=text("10"))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Read back for the API shapes (loaded with the row; refresh after a change).
    lat: Mapped[float] = column_property(func.ST_Y(func.geometry(location)))
    lng: Mapped[float] = column_property(func.ST_X(func.geometry(location)))
    service_area_geojson: Mapped[str | None] = column_property(func.ST_AsGeoJSON(service_area))


class Vendor(Base):
    """Someone a hub buys from. Deactivated, never deleted (purchase orders point at them)."""

    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint(_one_of("kind", VENDOR_KINDS), name="kind"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("NOT is_default OR active", name="default_is_active"),
        Index("uq_vendors_hub_name", "hub_id", text("lower(name)"), unique=True),
        # At most one default vendor per hub.
        Index(
            "uq_vendors_hub_default",
            "hub_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    phone_e164: Mapped[str | None] = mapped_column(String(16))
    whatsapp: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PackingTask(Base):
    """One order to pack (a box or an add-on). Its status follows the order: queued = locked."""

    __tablename__ = "packing_tasks"
    __table_args__ = (
        CheckConstraint(_one_of("status", TASK_STATUSES), name="status"),
        CheckConstraint(_one_of("order_kind", ("box", "addon")), name="order_kind"),
        CheckConstraint("bags BETWEEN 1 AND 5", name="bags"),
        CheckConstraint("crate_no IS NULL OR crate_no BETWEEN 1 AND 99", name="crate_no"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_packing_tasks_queue", "hub_id", "delivery_date", "status", "seq"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)  # M4
    order_kind: Mapped[str] = mapped_column(String(8))
    delivery_date: Mapped[date] = mapped_column(Date)
    slot_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M4
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M9
    # The household's address (M1) when the task was built; null if it has none.
    tower_id: Mapped[str | None] = mapped_column(String(20))
    flat: Mapped[str | None] = mapped_column(String(20))
    seq: Mapped[int] = mapped_column(Integer)  # place in the hub's queue for the date
    route_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M7, once routes are published
    stop_seq: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(8), server_default="queued")
    packer_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    bags: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    crate_no: Mapped[int | None] = mapped_column(SmallInteger)  # within the society
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    packed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PackingLine(Base):
    """An order line to pick, in steps; done when picked + short = qty."""

    __tablename__ = "packing_lines"
    __table_args__ = (
        CheckConstraint("qty >= 1", name="qty"),
        CheckConstraint(
            "picked_qty >= 0 AND short_qty >= 0 AND picked_qty + short_qty <= qty", name="picked"
        ),
        {"schema": SCHEMA},
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.packing_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # as ordered
    step_qty: Mapped[int] = mapped_column(Integer)
    step_unit: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    picked_qty: Mapped[int] = mapped_column(Integer, server_default=text("0"))  # of the substitute
    short_qty: Mapped[int] = mapped_column(Integer, server_default=text("0"))  # refunded
    substitute_product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PackingEvent(Base):
    """What a packer or hub manager did to a task, with the phone's tap time (audit, speed)."""

    __tablename__ = "packing_events"
    __table_args__ = (
        CheckConstraint(_one_of("action", PACKING_ACTIONS), name="action"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.packing_tasks.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(10))
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
