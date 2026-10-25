"""Tables of M6 Hub Operations. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Hubs (spec §3.1): hubs with their service area, and the vendors each hub buys from. Packing
(§3.2): one task per order to pack, its lines, and what packers did. Hub ids in other schemas, and
order, slot, society, product and user ids here, are plain UUIDs without a foreign key
(ADR-0026 §5). Procurement (§3.3): one plan per hub and delivery date with a line per stocked
product, and purchase orders per vendor. Receiving (§3.4): goods receipts, a lot per accepted line,
a ledger of stock moves and weekly vendor scores. Kept items and returned bags (§3.5): what a box
missed twice leaves on the shelf for its household. Waste logs and surplus offers (§3.5): what left
stock other than by packing. Operations quantities are NUMERIC(10,3) in the product's operations
unit; money is paise.
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
        CheckConstraint(
            "pre_live_box_fee_paise BETWEEN 0 AND 10000", name="pre_live_box_fee_paise"
        ),
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
    # The delivery fee on each box for a society that is not live yet (M9 §3.6), set by an admin.
    pre_live_box_fee_paise: Mapped[int] = mapped_column(BigInteger, server_default=text("3000"))
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
    """An order line to pick, in steps; done when picked + short = qty. A `carried` line is a
    kept item riding free on this box (M4 §3.8, §3.5): picked from the shelf, never short, and
    apart from a paid line of the same product."""

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
    carried: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("false"))
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
    # The kept item this move released or packed (§3.5), if any.
    kept_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.kept_items.id", ondelete="RESTRICT")
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


# --- waste and surplus (spec §3.5) ---------------------------------------------------------------

WASTE_REASONS = ("spoiled", "damaged", "expired", "pest", "surplus", "returned", "other")
CASCADE_TO = ("kitchen", "ngo", "bin")
OFFER_STATUSES = ("open", "taken", "declined", "expired")


class SurplusOffer(Base):
    """A lot's surplus offered to a kitchen or an NGO until the next 12:30 run moves it on."""

    __tablename__ = "surplus_offers"
    __table_args__ = (
        CheckConstraint(_one_of("unit", OPS_UNITS), name="unit"),
        CheckConstraint(_one_of("offered_to", ("kitchen", "ngo")), name="offered_to"),
        CheckConstraint(_one_of("status", OFFER_STATUSES), name="status"),
        CheckConstraint("qty > 0 AND (taken_qty IS NULL OR taken_qty >= 0)", name="qty"),
        Index("ix_surplus_offers_hub_id_status", "hub_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    lot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.inventory_lots.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M2
    qty: Mapped[Decimal] = _qty()
    unit: Mapped[str] = mapped_column(String(8))
    offered_to: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8), server_default="open")
    taken_qty: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    # The offer this one follows in the cascade (kitchen, then NGO).
    previous_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.surplus_offers.id", ondelete="RESTRICT")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M1; null: the 12:30 run
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WasteLog(Base):
    """A quantity that left stock other than by packing: written off by hub staff, or settled by
    the cascade (`cascade_to` is where it went; only `bin` is waste for the KPI). `kg` is its
    weight for the KPI, null when unknown."""

    __tablename__ = "waste_logs"
    __table_args__ = (
        CheckConstraint(_one_of("unit", OPS_UNITS), name="unit"),
        CheckConstraint(_one_of("reason", WASTE_REASONS), name="reason"),
        CheckConstraint(_one_of("cascade_to", CASCADE_TO), name="cascade_to"),
        CheckConstraint(
            "taken_by_kind IS NULL OR taken_by_kind IN ('kitchen', 'ngo')", name="taken_by_kind"
        ),
        CheckConstraint("qty > 0 AND (kg IS NULL OR kg >= 0)", name="qty"),
        Index("ix_waste_logs_hub_id_at", "hub_id", "at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.inventory_lots.id", ondelete="RESTRICT")
    )  # null: stock that was never received in the system
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M2
    qty: Mapped[Decimal] = _qty()
    unit: Mapped[str] = mapped_column(String(8))
    kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    reason: Mapped[str] = mapped_column(String(10))
    cascade_to: Mapped[str] = mapped_column(String(8))
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.surplus_offers.id", ondelete="RESTRICT")
    )
    taken_by_kind: Mapped[str | None] = mapped_column(String(8))
    taken_by_partner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M8
    taken_by_name: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(String(300))
    logged_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M1; null: the 12:30 job
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- kept items and returned bags (spec §3.5, ADR-0027 §2) --------------------------------------

KEPT_STATUSES = ("kept", "packed", "released", "lost")
STEP_UNITS = ("g", "kg", "ml", "l", "piece", "bunch", "pack")


class KeptItem(Base):
    """A non-perishable of a box missed twice, kept on a shelf for the household (M4's carry row
    `carry_id`): `kept` until a box carries it (`packed`), M4's 14 days pass (`released` into a
    returned lot) or the hub loses it (`lost`, refunded)."""

    __tablename__ = "kept_items"
    __table_args__ = (
        CheckConstraint(_one_of("status", KEPT_STATUSES), name="status"),
        CheckConstraint(_one_of("step_unit", STEP_UNITS), name="step_unit"),
        CheckConstraint("qty >= 1 AND step_qty >= 1", name="qty"),
        UniqueConstraint("carry_id", "bag_order_id"),
        Index("ix_kept_items_hub_id_status", "hub_id", "status"),
        Index("ix_kept_items_household_id", "household_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1
    carry_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M4's orders.carry_overs row
    from_order_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M4: the box missed twice
    bag_order_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M4: the bag that was scanned
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M9
    tower_id: Mapped[str | None] = mapped_column(String(20))
    flat: Mapped[str | None] = mapped_column(String(20))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M2
    step_qty: Mapped[int] = mapped_column(Integer)
    step_unit: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)  # steps
    status: Mapped[str] = mapped_column(String(8), server_default="kept")
    packed_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.packing_tasks.id", ondelete="RESTRICT")
    )
    kept_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReturnedBag(Base):
    """A returned bag that was unpacked, once (the order id is the key): a second scan replays."""

    __tablename__ = "returned_bags"
    __table_args__ = ({"schema": SCHEMA},)

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # M4
    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.hubs.id", ondelete="RESTRICT"))
    scanned_by: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1 user
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
