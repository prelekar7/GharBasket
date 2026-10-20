"""Tables of M4 Orders & Checkout, in the `orders` schema (spec §4).

An order is the record of a box after approval (and, later, a tiffin or a direct add-on order).
Its lines carry the prices frozen when they were made; its events are the timeline. Slots belong
to a hub and have a capacity per date, counted in `slot_bookings`. Issues are problems reported
on a dispatched or delivered order (§3.2), with the lines they claim.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "orders"

ORDER_KINDS = ("box", "tiffin", "addon")
LINE_KINDS = ("box", "addon")
SLOT_KINDS = ("box", "tiffin")
MEALS = ("lunch", "dinner")  # a tiffin slot's meal (§3.5)
STATUSES = (
    "approved",
    "locked",
    "payment_failed",
    "cancelled",
    "picking",
    "packed",
    "dispatched",
    "at_society",
    "delivered",
    "undelivered",
    "redelivery",
    "refunded",
)
ACTORS = ("customer", "system", "hub", "packer", "rider", "captain", "kitchen")
UNITS = ("g", "kg", "ml", "l", "piece", "bunch", "pack")
ISSUE_KINDS = ("missing", "damaged", "quality", "wrong_item", "late", "other")
ISSUE_STATUSES = ("open", "credited", "rejected")
ISSUE_RESOLVERS = ("auto", "hub")
OPEN_REASONS = (
    "photo_needed",
    "over_auto_credit_limit",
    "over_order_total",
    "wallet_limit",
    "triage",
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (
        CheckConstraint(_one_of("kind", SLOT_KINDS), name="kind"),
        CheckConstraint("end_time > start_time", name="times"),
        CheckConstraint("capacity BETWEEN 1 AND 2000", name="capacity"),
        CheckConstraint(
            f"(kind = 'tiffin' AND {_one_of('meal', MEALS)})"
            " OR (kind <> 'tiffin' AND meal IS NULL)",
            name="meal",
        ),
        UniqueConstraint("hub_id", "label"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M6's hub; no cross-schema FK
    label: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(8), server_default="box")
    start_time: Mapped[time] = mapped_column(Time)  # IST clock time
    end_time: Mapped[time] = mapped_column(Time)
    capacity: Mapped[int] = mapped_column(SmallInteger)
    # lunch or dinner for a tiffin slot, so the server picks it (§3.5); null for box slots.
    meal: Mapped[str | None] = mapped_column(String(8))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SlotBooking(Base):
    """One row per order in a slot on a date; counting them is the slot's load."""

    __tablename__ = "slot_bookings"
    __table_args__ = (UniqueConstraint("order_id"), {"schema": SCHEMA})

    slot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.slots.id", ondelete="RESTRICT"), primary_key=True
    )
    on_date: Mapped[date] = mapped_column("date", Date, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.orders.id", ondelete="CASCADE"), primary_key=True
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(_one_of("kind", ORDER_KINDS), name="kind"),
        CheckConstraint(_one_of("status", STATUSES), name="status"),
        CheckConstraint(
            "subtotal_paise >= 0 AND fees_paise >= 0 AND discount_paise >= 0 AND total_paise >= 0",
            name="money",
        ),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        CheckConstraint(
            "kind = 'tiffin' OR (tiffin_menu_id IS NULL AND preferred_partner_id IS NULL"
            " AND portions IS NULL)",
            name="tiffin_only",
        ),
        CheckConstraint("portions IS NULL OR portions BETWEEN 1 AND 12", name="portions"),
        CheckConstraint("kind = 'tiffin' OR kitchen_partner_id IS NULL", name="kitchen"),
        # One live order per weekly cycle: a skip cancels it, and an un-skipped week approved
        # again gets a new one (M4 §4, M3 §3.3).
        Index(
            "uq_orders_cycle_id_active",
            "cycle_id",
            unique=True,
            postgresql_where=text("status <> 'cancelled'"),
        ),
        Index("ix_orders_household_delivery", "household_id", "delivery_date"),
        Index("ix_orders_hub_delivery_status", "hub_id", "delivery_date", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M9
    kind: Mapped[str] = mapped_column(String(8))
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M3's weekly cycle
    delivery_date: Mapped[date] = mapped_column(Date)
    slot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.slots.id", ondelete="RESTRICT")
    )
    # 21:00 IST on the day before delivery: the API refuses edits after this moment itself.
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    subtotal_paise: Mapped[int] = mapped_column(BigInteger)
    fees_paise: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    discount_paise: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    total_paise: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Tiffins (§3.5): the booked menu (M2), its kitchen as the preferred cook (M8 §3.4) and
    # the portions; null for boxes and add-ons.
    tiffin_menu_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    preferred_partner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    portions: Mapped[int | None] = mapped_column(SmallInteger)
    # The kitchen that cooks it, set by M8 when she accepts and cleared on a cancellation
    # (`set_kitchen`, ADR-0026 §3); null until then.
    kitchen_partner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint(_one_of("kind", LINE_KINDS), name="kind"),
        CheckConstraint(_one_of("step_unit", UNITS), name="step_unit"),
        CheckConstraint("qty > 0 AND step_qty > 0", name="qty"),
        CheckConstraint("unit_price_paise >= 0 AND shortage_qty >= 0", name="amounts"),
        {"schema": SCHEMA},
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.orders.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # M2
    kind: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)  # steps
    step_qty: Mapped[int] = mapped_column(Integer)
    step_unit: Mapped[str] = mapped_column(String(8))
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)  # per step, frozen
    reason_code: Mapped[str | None] = mapped_column(String(40))
    reason_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    substituted_from: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    shortage_qty: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    @property
    def total_paise(self) -> int:
        """What the delivered steps cost: steps short at packing are refunded (§3.1)."""
        return (self.qty - (self.shortage_qty or 0)) * self.unit_price_paise


class OrderEvent(Base):
    """The timeline: one row per status change (and the creation)."""

    __tablename__ = "order_events"
    __table_args__ = (
        CheckConstraint(_one_of("actor", ACTORS), name="actor"),
        Index("ix_order_events_order", "order_id", "id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.orders.id", ondelete="CASCADE")
    )
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(10))  # the kind of actor; the timeline shows this
    # The person who made the move (packer, rider, captain, kitchen partner, hub manager or the
    # customer), when there is one; never shown to customers (ADR-0026 §2).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderIssue(Base):
    """A problem reported on an order (§3.2): credited at once, or open for the hub's triage."""

    __tablename__ = "order_issues"
    __table_args__ = (
        CheckConstraint(_one_of("kind", ISSUE_KINDS), name="kind"),
        CheckConstraint(_one_of("status", ISSUE_STATUSES), name="status"),
        CheckConstraint(_one_of("resolved_by", ISSUE_RESOLVERS), name="resolved_by"),
        CheckConstraint(_one_of("open_reason", OPEN_REASONS), name="open_reason"),
        CheckConstraint("(status = 'open') = (open_reason IS NOT NULL)", name="open_reason_open"),
        CheckConstraint("amount_paise >= 0 AND credit_paise >= 0", name="money"),
        CheckConstraint("cardinality(photo_keys) <= 3", name="photos"),
        Index("ix_order_issues_order", "order_id"),
        # The rolling 7-day auto-credit allowance per household.
        Index(
            "ix_order_issues_household_auto",
            "household_id",
            "resolved_at",
            postgresql_where=text("resolved_by = 'auto'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.orders.id", ondelete="CASCADE")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10))
    open_reason: Mapped[str | None] = mapped_column(String(24))  # why it was not auto-credited
    note: Mapped[str | None] = mapped_column(String(500))
    photo_keys: Mapped[list[str]] = mapped_column(ARRAY(String(200)))  # claimed by "orders"
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    credit_paise: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    resolved_by: Mapped[str | None] = mapped_column(String(4))
    resolver_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M5's journal entry
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderIssueItem(Base):
    __tablename__ = "order_issue_items"
    __table_args__ = (
        CheckConstraint("qty > 0 AND unit_price_paise >= 0", name="amounts"),
        {"schema": SCHEMA},
    )

    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.order_issues.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # a line of the order
    qty: Mapped[int] = mapped_column(Integer)  # steps
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)  # the line's paid price


CARRY_STATUSES = ("pending", "attached", "packed", "delivered", "refunded")
CARRY_REFUNDERS = ("expiry", "hub")


class CarryOver(Base):
    """A non-perishable line of a box missed twice, kept for the household's next box (§3.8).

    Not an order line: one row per (missed order, product), so it never collides with a paid line
    of the same product and never counts towards totals, debits, shortages or procurement."""

    __tablename__ = "carry_overs"
    __table_args__ = (
        CheckConstraint(_one_of("status", CARRY_STATUSES), name="status"),
        CheckConstraint(
            "refunded_by IS NULL OR " + _one_of("refunded_by", CARRY_REFUNDERS), name="refunded_by"
        ),
        CheckConstraint("steps > 0 AND step_qty > 0 AND paid_paise >= 0", name="amounts"),
        UniqueConstraint("from_order_id", "product_id"),
        Index("ix_carry_overs_household_id_status", "household_id", "status"),
        Index("ix_carry_overs_to_order_id", "to_order_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    from_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.orders.id"))
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M2
    steps: Mapped[int] = mapped_column(Integer)
    step_qty: Mapped[int] = mapped_column(Integer)
    step_unit: Mapped[str] = mapped_column(String(8))
    paid_paise: Mapped[int] = mapped_column(BigInteger)  # what those steps cost
    missed_on: Mapped[date] = mapped_column(Date)  # the missed order's delivery date
    expires_on: Mapped[date] = mapped_column(Date)  # missed_on + 14 days (ADR-0027 §6)
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    to_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.orders.id"))
    # The order it rode in and came back from (that order was missed twice too).
    returned_in_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.orders.id")
    )
    refunded_by: Mapped[str | None] = mapped_column(String(6))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
