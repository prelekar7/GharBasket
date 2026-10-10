"""Tables of M3 Plans & Subscriptions, in the `subscriptions` schema (spec §4).

A household has one subscription; each delivery day of its society gets a weekly cycle: the
drafted box (lines with reasons), edited and approved before the cut-off, then handed to M4.
"""

import uuid
from datetime import date, datetime
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
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "subscriptions"

PLAN_CODES = ("autopilot", "tiffin", "salad")
SUBSCRIPTION_STATUSES = ("active", "paused", "ended")
PAYMENT_MODES = ("wallet", "autopay")
CYCLE_STATUSES = (
    "draft",
    "awaiting_approval",
    "approved",
    "skipped",
    "cancelled",
    "locked",
    "payment_failed",  # mirrored from the order: the cut-off found the wallet short (§3.3)
)
PLAN_SOURCES = ("ml", "fallback")
LINE_SOURCES = ("ai", "rule", "customer")
REASON_CODES = (
    "meal_plan",
    "daily_use",
    "in_season",
    "price_swap",
    "swapped_by_you",
    "added_by_you",
    "fallback_rules",
    "health_tag",
    "pantry_low",
)
PRODUCT_ACTIONS = ("removed", "added", "qty_up", "qty_down", "swapped")
MEAL_ACTIONS = ("meal_swapped", "meal_tiffin", "meal_no_cook", "meal_restored")
EDIT_ACTIONS = PRODUCT_ACTIONS + MEAL_ACTIONS
PANTRY_SIGNALS = ("ran_out", "wasted", "just_right")
UNITS = ("g", "kg", "ml", "l", "piece", "bunch", "pack")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (CheckConstraint(_one_of("code", PLAN_CODES), name="code"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    code: Mapped[str] = mapped_column(String(16), unique=True)
    names: Mapped[dict[str, str]] = mapped_column(JSONB)
    fee_paise: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    features: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(_one_of("status", SUBSCRIPTION_STATUSES), name="status"),
        CheckConstraint(_one_of("payment_mode", PAYMENT_MODES), name="payment_mode"),
        UniqueConstraint("household_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.plans.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(8), server_default="active")
    payment_mode: Mapped[str] = mapped_column(String(8))
    paused_until: Mapped[date | None] = mapped_column(Date)
    auto_approve: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SocietyDeliveryDay(Base):
    """Which weekday(s) a society gets boxes, and in which of its hub's slots."""

    __tablename__ = "society_delivery_days"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        {"schema": SCHEMA},
    )

    society_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # M9
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 0 = Monday
    slot_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M4's slot


class BudgetLock(Base):
    __tablename__ = "budget_locks"
    __table_args__ = (
        CheckConstraint("budget_paise >= 50000", name="budget_paise"),
        UniqueConstraint("household_id", "month"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    month: Mapped[date] = mapped_column(Date)  # the first of the month
    budget_paise: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WeeklyCycle(Base):
    __tablename__ = "weekly_cycles"
    __table_args__ = (
        CheckConstraint(_one_of("status", CYCLE_STATUSES), name="status"),
        CheckConstraint(_one_of("plan_source", PLAN_SOURCES), name="plan_source"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        UniqueConstraint("household_id", "delivery_date"),
        Index("ix_weekly_cycles_hub_delivery", "hub_id", "delivery_date", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.subscriptions.id", ondelete="CASCADE")
    )
    week_start: Mapped[date] = mapped_column(Date)  # the Monday
    delivery_date: Mapped[date] = mapped_column(Date)
    slot_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    plan_prediction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # intel.predictions
    plan_source: Mapped[str] = mapped_column(String(10))
    # The week's meals as shown: [{date, meals: [{slot, recipe_id, recipe_slug, tiffin, sides}]}];
    # a meal the customer set (M3 §3.2) adds source "customer" and keeps the planner's in "planned"
    meal_plan: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    highlights: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(String(20))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When `remind_unapproved` sent the cut-off reminder: at most one per cycle.
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_approved: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M4
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BoxLine(Base):
    __tablename__ = "box_lines"
    __table_args__ = (
        CheckConstraint(_one_of("source", LINE_SOURCES), name="source"),
        CheckConstraint(_one_of("reason_code", REASON_CODES), name="reason_code"),
        CheckConstraint(_one_of("step_unit", UNITS), name="step_unit"),
        CheckConstraint("qty > 0 AND step_qty > 0 AND unit_price_paise >= 0", name="amounts"),
        {"schema": SCHEMA},
    )

    cycle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.weekly_cycles.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # M2
    qty: Mapped[int] = mapped_column(Integer)  # steps
    step_qty: Mapped[int] = mapped_column(Integer)
    step_unit: Mapped[str] = mapped_column(String(8))
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)  # today's hub price; frozen by M4
    reason_code: Mapped[str] = mapped_column(String(20))
    reason_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(10))
    replaces_product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class EditFeedback(Base):
    """What the customer changed on a box: the ranker's labels (handbook 08 M-B). A product row
    names the product; a meal row (M3 §3.2) names the meal and its recipe before and after."""

    __tablename__ = "edit_feedback"
    __table_args__ = (
        CheckConstraint(_one_of("action", EDIT_ACTIONS), name="action"),
        CheckConstraint(
            f"CASE WHEN {_one_of('action', MEAL_ACTIONS)} "
            "THEN product_id IS NULL AND meal_date IS NOT NULL AND slot IS NOT NULL "
            "ELSE product_id IS NOT NULL END",
            name="target",
        ),
        Index("ix_edit_feedback_cycle", "cycle_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.weekly_cycles.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(16))
    substitute_product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    meal_date: Mapped[date | None] = mapped_column(Date)
    slot: Mapped[str | None] = mapped_column(String(10))
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M2, before the change
    substitute_recipe_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # after
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PantryFeedback(Base):
    """After a delivery: ran out, wasted or just right per item (the pantry twin's signals)."""

    __tablename__ = "pantry_feedback"
    __table_args__ = (
        CheckConstraint(_one_of("signal", PANTRY_SIGNALS), name="signal"),
        UniqueConstraint("cycle_id", "product_id"),
        Index("ix_pantry_feedback_household", "household_id", "at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.weekly_cycles.id", ondelete="CASCADE")
    )
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    signal: Mapped[str] = mapped_column(String(10))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
