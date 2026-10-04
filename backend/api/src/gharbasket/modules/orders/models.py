"""Tables of M4 Orders & Checkout, in the `orders` schema (spec §4).

An order is the record of a box after approval (and, later, a tiffin or a direct add-on order).
Its lines carry the prices frozen when they were made; its events are the timeline. Slots belong
to a hub and have a capacity per date, counted in `slot_bookings`.
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "orders"

ORDER_KINDS = ("box", "tiffin", "addon")
LINE_KINDS = ("box", "addon")
SLOT_KINDS = ("box", "tiffin")
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
ACTORS = ("customer", "system", "hub")
UNITS = ("g", "kg", "ml", "l", "piece", "bunch", "pack")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (
        CheckConstraint(_one_of("kind", SLOT_KINDS), name="kind"),
        CheckConstraint("end_time > start_time", name="times"),
        CheckConstraint("capacity BETWEEN 1 AND 2000", name="capacity"),
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
        UniqueConstraint("cycle_id"),
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
        return self.qty * self.unit_price_paise


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
    actor: Mapped[str] = mapped_column(String(10))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
