"""Tables of M6 Hub Operations. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Hubs (spec §3.1): hubs with their service area, and the vendors each hub buys from. Hub ids
in other schemas are plain UUIDs without a foreign key (ADR-0026 §5). Procurement (§3.3): one
plan per hub and delivery date with a line per stocked product, and purchase orders per vendor.
Receiving (§3.4): goods receipts, a lot per accepted line, a ledger of stock moves and weekly
vendor scores. Operations quantities are NUMERIC(10,3) in the product's operations unit; money
is paise.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography, WKBElement, WKTElement
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
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


# --- procurement (spec §3.3) ---------------------------------------------------------------------

PLAN_STATUSES = ("draft", "ordered", "closed")
PO_STATUSES = ("draft", "sent", "partly_received", "received", "cancelled")
OPS_UNITS = ("kg", "l", "piece", "bunch", "pack")


def _qty() -> Mapped[Decimal]:
    return mapped_column(Numeric(10, 3))


class ProcurementPlan(Base):
    """What a hub buys for one delivery date: built at T-1 21:10, rebuilt until it closes."""

    __tablename__ = "procurement_plans"
    __table_args__ = (
        UniqueConstraint("hub_id", "delivery_date"),
        CheckConstraint(_one_of("status", PLAN_STATUSES), name="status"),
        CheckConstraint(_one_of("source", ("ml", "fallback")), name="source"),
        CheckConstraint("buffer_pct BETWEEN 0 AND 50", name="buffer_pct"),
        CheckConstraint("version >= 1", name="version"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    delivery_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8), server_default="draft")
    source: Mapped[str] = mapped_column(String(8))
    model: Mapped[str | None] = mapped_column(String(60))
    buffer_pct: Mapped[int] = mapped_column(SmallInteger)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Counts changes to the lines; a draft PO made at an older version is stale.
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcurementLine(Base):
    __tablename__ = "procurement_lines"
    __table_args__ = (
        CheckConstraint(_one_of("unit", OPS_UNITS), name="unit"),
        CheckConstraint(_one_of("basis", ("forecast", "buffer")), name="basis"),
        CheckConstraint(
            "committed_qty >= 0 AND at_risk_qty >= 0 AND buffer_qty >= 0 AND on_hand_qty >= 0"
            " AND suggested_qty >= 0 AND order_qty >= 0",
            name="qty",
        ),
        CheckConstraint("unit_cost_paise >= 0", name="unit_cost_paise"),
        {"schema": SCHEMA},
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.procurement_plans.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # M2
    unit: Mapped[str] = mapped_column(String(8))
    shelf_life_h: Mapped[int | None] = mapped_column(Integer)
    committed_qty: Mapped[Decimal] = _qty()
    at_risk_qty: Mapped[Decimal] = _qty()
    forecast_q50: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    forecast_qtau: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    basis: Mapped[str] = mapped_column(String(8))
    buffer_qty: Mapped[Decimal] = _qty()
    on_hand_qty: Mapped[Decimal] = _qty()
    suggested_qty: Mapped[Decimal] = _qty()
    order_qty: Mapped[Decimal] = _qty()
    edited: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    edit_reason: Mapped[str | None] = mapped_column(String(200))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.vendors.id", ondelete="RESTRICT")
    )
    unit_cost_paise: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PurchaseOrder(Base):
    """One vendor's part of a plan: a draft until the hub manager sends it herself."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint(_one_of("status", PO_STATUSES), name="status"),
        CheckConstraint("total_paise >= 0 AND unpriced_lines >= 0", name="totals"),
        CheckConstraint("version >= 1", name="version"),
        # One draft per vendor and plan; generating again replaces the drafts.
        Index(
            "uq_purchase_orders_plan_vendor_draft",
            "plan_id",
            "vendor_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        Index("ix_purchase_orders_hub_delivery", "hub_id", "delivery_date"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    number: Mapped[str | None] = mapped_column(String(60), unique=True)  # set when sent
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.procurement_plans.id", ondelete="RESTRICT")
    )
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vendors.id", ondelete="RESTRICT")
    )
    delivery_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), server_default="draft")
    plan_version: Mapped[int] = mapped_column(Integer)
    total_paise: Mapped[int] = mapped_column(BigInteger)
    unpriced_lines: Mapped[int] = mapped_column(Integer)
    share_text: Mapped[str] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M1 user
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        CheckConstraint(_one_of("unit", OPS_UNITS), name="unit"),
        CheckConstraint("qty > 0 AND received_qty >= 0 AND accepted_qty >= 0", name="qty"),
        {"schema": SCHEMA},
    )

    po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.purchase_orders.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # M2
    unit: Mapped[str] = mapped_column(String(8))
    qty: Mapped[Decimal] = _qty()
    unit_cost_paise: Mapped[int | None] = mapped_column(BigInteger)
    amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    received_qty: Mapped[Decimal] = mapped_column(Numeric(10, 3), server_default=text("0"))
    accepted_qty: Mapped[Decimal] = mapped_column(Numeric(10, 3), server_default=text("0"))


# --- receiving and stock (spec §3.4) -------------------------------------------------------------

GRADES = ("A", "B", "C")
REJECT_REASONS = ("rotten", "damaged", "undersized", "wrong_item", "other")
LOT_SOURCES = ("receipt", "returned")
MOVE_KINDS = ("receive", "return", "pack", "waste", "offer", "count")


class GoodsReceipt(Base):
    """What a vendor delivered, weighed and graded at the dock. A fact: never edited."""

    __tablename__ = "goods_receipts"
    __table_args__ = (
        CheckConstraint("total_paise >= 0 AND unpriced_lines >= 0", name="totals"),
        Index("ix_goods_receipts_hub_received", "hub_id", "received_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vendors.id", ondelete="RESTRICT")
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.purchase_orders.id", ondelete="RESTRICT"), index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1 user
    total_paise: Mapped[int] = mapped_column(BigInteger)
    unpriced_lines: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"
    __table_args__ = (
        UniqueConstraint("receipt_id", "product_id"),
        CheckConstraint(_one_of("unit", OPS_UNITS), name="unit"),
        CheckConstraint(_one_of("grade", GRADES), name="grade"),
        CheckConstraint(_one_of("reject_reason", REJECT_REASONS), name="reject_reason"),
        CheckConstraint("received_qty > 0 AND accepted_qty BETWEEN 0 AND received_qty", name="qty"),
        # A reason exactly when something went back to the vendor.
        CheckConstraint(
            "(accepted_qty < received_qty) = (reject_reason IS NOT NULL)", name="reject"
        ),
        CheckConstraint("unit_cost_paise >= 0", name="unit_cost_paise"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.goods_receipts.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M2
    unit: Mapped[str] = mapped_column(String(8))
    received_qty: Mapped[Decimal] = _qty()
    accepted_qty: Mapped[Decimal] = _qty()
    grade: Mapped[str] = mapped_column(String(1))
    reject_reason: Mapped[str | None] = mapped_column(String(16))
    unit_cost_paise: Mapped[int | None] = mapped_column(BigInteger)
    amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    best_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ordered: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(String(200))


class InventoryLot(Base):
    """Stock of one product from one receipt line (or a returned bag, §3.5). `qty_on_hand` is the
    sum of the lot's stock moves."""

    __tablename__ = "inventory_lots"
    __table_args__ = (
        CheckConstraint(_one_of("unit", OPS_UNITS), name="unit"),
        CheckConstraint(_one_of("source", LOT_SOURCES), name="source"),
        CheckConstraint(_one_of("grade", GRADES), name="grade"),
        CheckConstraint("qty_in >= 0 AND qty_on_hand >= 0", name="qty"),
        CheckConstraint("unit_cost_paise >= 0", name="unit_cost_paise"),
        Index(
            "ix_inventory_lots_stock",
            "hub_id",
            "product_id",
            "best_before",
            postgresql_where=text("qty_on_hand > 0"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M2
    unit: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(8))
    receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.goods_receipt_lines.id", ondelete="RESTRICT"), unique=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M4, returned lots
    grade: Mapped[str] = mapped_column(String(1))
    qty_in: Mapped[Decimal] = _qty()
    qty_on_hand: Mapped[Decimal] = _qty()
    unit_cost_paise: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    best_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StockMove(Base):
    """One change to stock (signed). Unique per kind, source row, product and lot, so a replayed
    request or job never moves stock twice. A null lot is unlotted packing (§3.5)."""

    __tablename__ = "stock_moves"
    __table_args__ = (
        UniqueConstraint(
            "kind", "ref_id", "product_id", "lot_id", postgresql_nulls_not_distinct=True
        ),
        CheckConstraint(_one_of("kind", MOVE_KINDS), name="kind"),
        CheckConstraint(_one_of("unit", OPS_UNITS), name="unit"),
        Index("ix_stock_moves_lot", "lot_id", "at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.inventory_lots.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M2
    unit: Mapped[str] = mapped_column(String(8))
    qty: Mapped[Decimal] = _qty()
    kind: Mapped[str] = mapped_column(String(8))
    # The receipt line, packing task, waste log, offer or count that made it.
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    reason: Mapped[str | None] = mapped_column(String(200))
    by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M1; null: a job
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _score() -> Mapped[Decimal | None]:
    return mapped_column(Numeric(4, 3))


class VendorScore(Base):
    """A vendor's week (Monday to Sunday, IST): components in 0 … 1, null without data."""

    __tablename__ = "vendor_scores"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 1", name="score"),
        CheckConstraint("pos_count >= 0 AND receipt_lines_count >= 0", name="counts"),
        {"schema": SCHEMA},
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.vendors.id", ondelete="RESTRICT"), primary_key=True
    )
    week: Mapped[date] = mapped_column(Date, primary_key=True)  # the Monday
    freshness: Mapped[Decimal | None] = _score()
    price: Mapped[Decimal | None] = _score()
    reliability: Mapped[Decimal | None] = _score()
    defects: Mapped[Decimal | None] = _score()  # the rejected share; counts as 1 - defects
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    pos_count: Mapped[int] = mapped_column(Integer)
    receipt_lines_count: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
