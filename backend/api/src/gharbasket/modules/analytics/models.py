"""Tables of M12 Console & Analytics. Every table sets `__table_args__ = {"schema": SCHEMA}`.

The fact tables (spec §3.2) are M12's own copy of the source modules' facts, pulled through their
`api.py` facades by the refresh jobs (§3.1): ids, dates, statuses, counts, grams and paise, never
a name, phone, flat, address or health field. A fact's `synced_at` moves only when one of its
values changed. Ids of other modules are plain UUIDs without foreign keys.

Also the unit-economics assumptions with their history (§3.4) and the refresh runs (§3.2). The
materialised views over the fact tables are made by the migration (they are not ORM models).
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
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "analytics"

DATA_SOURCES = ("real", "synthetic")
COMMUNITY_KINDS = (
    "donation_verified",
    "referral_converted",
    "handoff_items",
    "handoff_food",
    "handoff_meals",
    "meals_pledged",
    "society_live",
)
JOBS = ("refresh_analytics", "refresh_analytics_nightly", "rebuild_analytics")
RUN_STATUSES = ("running", "ok", "failed")
ASSUMPTION_KEYS = (
    "aov_paise",
    "margin_bp",
    "delivery_paise",
    "packing_paise",
    "captain_paise",
    "payment_fee_bp",
    "cac_paise",
)
# The defaults (hub_id null), decided 2026-10-09 (§3.4, §11); the migration inserts them.
DEFAULT_ASSUMPTIONS = {
    "aov_paise": 62_000,
    "margin_bp": 2_400,
    "delivery_paise": 4_000,
    "packing_paise": 3_000,
    "captain_paise": 600,
    "payment_fee_bp": 120,
    "cac_paise": 1_300,
}


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _data_source() -> CheckConstraint:
    return CheckConstraint(_one_of("data_source", DATA_SOURCES), name="data_source")


def _synced_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class FactHousehold(Base):
    __tablename__ = "fact_households"
    __table_args__ = (_data_source(), Index(None, "cohort_week"), {"schema": SCHEMA})

    household_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_on: Mapped[date] = mapped_column(Date)  # IST
    cohort_week: Mapped[date] = mapped_column(Date)  # the Monday of created_on
    status: Mapped[str] = mapped_column(String(16))
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class FactCycle(Base):
    __tablename__ = "fact_cycles"
    __table_args__ = (
        _data_source(),
        Index(None, "delivery_date", "hub_id"),
        Index(None, "household_id"),
        {"schema": SCHEMA},
    )

    cycle_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    week_start: Mapped[date] = mapped_column(Date)
    delivery_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))
    auto_approved: Mapped[bool] = mapped_column(Boolean)
    approved_before_cutoff: Mapped[bool] = mapped_column(Boolean)
    edits: Mapped[int] = mapped_column(Integer)
    plan_source: Mapped[str] = mapped_column(String(10))
    ran_out_taps: Mapped[int] = mapped_column(Integer)
    wasted_taps: Mapped[int] = mapped_column(Integer)
    # Not in §3.2's first draft: `pantry_misses_bp` needs every tap as its denominator.
    just_right_taps: Mapped[int] = mapped_column(Integer)
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class FactOrder(Base):
    __tablename__ = "fact_orders"
    __table_args__ = (
        _data_source(),
        CheckConstraint(
            "total_paise >= 0 AND fees_paise >= 0 AND refunded_paise >= 0 AND credited_paise >= 0",
            name="money",
        ),
        Index(None, "delivery_date", "hub_id"),
        Index(None, "household_id"),
        {"schema": SCHEMA},
    )

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(8))
    delivery_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    total_paise: Mapped[int] = mapped_column(BigInteger)
    # Not in §3.2's first draft: §3.4's delivery-fee revenue is Σ fees of delivered boxes.
    fees_paise: Mapped[int] = mapped_column(BigInteger)
    refunded_paise: Mapped[int] = mapped_column(BigInteger)
    credited_paise: Mapped[int] = mapped_column(BigInteger)
    lines: Mapped[int] = mapped_column(Integer)
    short_lines: Mapped[int] = mapped_column(Integer)
    on_time: Mapped[bool | None] = mapped_column(Boolean)  # null until delivered
    first_attempt: Mapped[bool | None] = mapped_column(Boolean)  # null until dispatched
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class FactIssue(Base):
    __tablename__ = "fact_issues"
    __table_args__ = (_data_source(), Index(None, "order_id"), {"schema": SCHEMA})

    issue_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    credit_paise: Mapped[int] = mapped_column(BigInteger)
    resolved_by: Mapped[str | None] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class FactRoute(Base):
    __tablename__ = "fact_routes"
    __table_args__ = (_data_source(), Index(None, "date", "hub_id"), {"schema": SCHEMA})

    route_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    on_date: Mapped[date] = mapped_column("date", Date)
    kind: Mapped[str] = mapped_column(String(8))
    km_m: Mapped[int] = mapped_column(Integer)
    cost_paise: Mapped[int | None] = mapped_column(BigInteger)
    orders: Mapped[int] = mapped_column(Integer)
    stops: Mapped[int] = mapped_column(Integer)
    on_time_stops: Mapped[int] = mapped_column(Integer)
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class FactHubDay(Base):
    __tablename__ = "fact_hub_days"
    __table_args__ = (_data_source(), {"schema": SCHEMA})

    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    on_date: Mapped[date] = mapped_column("date", Date, primary_key=True)
    packed_g: Mapped[int] = mapped_column(BigInteger)
    waste_ngo_g: Mapped[int] = mapped_column(BigInteger)
    waste_bin_g: Mapped[int] = mapped_column(BigInteger)
    waste_kitchen_g: Mapped[int] = mapped_column(BigInteger)
    waste_imperfect_g: Mapped[int] = mapped_column(BigInteger)
    cogs_paise: Mapped[int | None] = mapped_column(BigInteger)
    surplus_offered_g: Mapped[int] = mapped_column(BigInteger)
    surplus_taken_g: Mapped[int] = mapped_column(BigInteger)
    # Not in §3.2's first draft: the hub's, so hub-level numbers are never pooled (§3.3).
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class FactPartnerWeek(Base):
    __tablename__ = "fact_partner_weeks"
    __table_args__ = (_data_source(), Index(None, "iso_week", "hub_id"), {"schema": SCHEMA})

    partner_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    iso_week: Mapped[date] = mapped_column(Date, primary_key=True)  # its Monday
    basis: Mapped[str] = mapped_column(String(20), primary_key=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(16))  # the partner's: kitchen, captain, rider, packer
    units: Mapped[int] = mapped_column(Integer)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    data_source: Mapped[str] = mapped_column(String(16))  # the partner's
    synced_at: Mapped[datetime] = _synced_at()


class FactAssignmentRun(Base):
    __tablename__ = "fact_assignment_runs"
    __table_args__ = (_data_source(), Index(None, "date", "hub_id"), {"schema": SCHEMA})

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    on_date: Mapped[date] = mapped_column("date", Date)
    meal: Mapped[str] = mapped_column(String(8))
    portions: Mapped[int] = mapped_column(Integer)
    gini_bp: Mapped[int | None] = mapped_column(Integer)
    km_per_portion_m: Mapped[int | None] = mapped_column(Integer)
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class FactCommunity(Base):
    __tablename__ = "fact_community"
    __table_args__ = (
        _data_source(),
        CheckConstraint(_one_of("kind", COMMUNITY_KINDS), name="kind"),
        Index(None, "date", "kind"),
        {"schema": SCHEMA},
    )

    source: Mapped[str] = mapped_column(String(16), primary_key=True)  # donation, referral, …
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ngo_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    on_date: Mapped[date] = mapped_column("date", Date)
    kind: Mapped[str] = mapped_column(String(20))
    items: Mapped[int] = mapped_column(Integer)
    kg_tenths: Mapped[int] = mapped_column(Integer)
    meals: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    people_reached: Mapped[int | None] = mapped_column(Integer)
    household_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    data_source: Mapped[str] = mapped_column(String(16))
    synced_at: Mapped[datetime] = _synced_at()


class Assumption(Base):
    """A unit-economics assumption (§3.4) of one hub, or the default when `hub_id` is null."""

    __tablename__ = "assumptions"
    __table_args__ = (
        UniqueConstraint("hub_id", "key", postgresql_nulls_not_distinct=True),
        CheckConstraint(_one_of("key", ASSUMPTION_KEYS), name="key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    key: Mapped[str] = mapped_column(String(20))
    value: Mapped[int] = mapped_column(BigInteger)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # M1's user; null: the default
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssumptionVersion(Base):
    """The version of one hub's assumptions: the ETag of the GET and the If-Match of the PUT
    (§3.4). A hub without a row is at version 1 (the defaults); each PUT that changes a value
    adds 1. The PUT locks the row, so two writers holding the same version cannot both win."""

    __tablename__ = "assumption_versions"
    __table_args__ = (CheckConstraint("version >= 1", name="version"), {"schema": SCHEMA})

    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class AssumptionChange(Base):
    """One changed key of one assumptions PUT: the history the console shows (§3.4)."""

    __tablename__ = "assumption_changes"
    __table_args__ = (
        CheckConstraint(_one_of("key", ASSUMPTION_KEYS), name="key"),
        Index(None, "hub_id", "at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    key: Mapped[str] = mapped_column(String(20))
    from_value: Mapped[int | None] = mapped_column(BigInteger)
    to_value: Mapped[int] = mapped_column(BigInteger)
    by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The hub's assumptions version this change produced: the keys of one PUT share it.
    version: Mapped[int] = mapped_column(Integer, server_default="2")


class RefreshRun(Base):
    """One run of a refresh job (§3.2): its window, the rows read per fact and how it ended. The
    last `ok` run of a job gives the `as_of` of the views it refreshes."""

    __tablename__ = "refresh_runs"
    __table_args__ = (
        CheckConstraint(_one_of("job", JOBS), name="job"),
        CheckConstraint(_one_of("status", RUN_STATUSES), name="status"),
        CheckConstraint("window_to >= window_from", name="window"),
        Index(None, "job", "status", "finished_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    job: Mapped[str] = mapped_column(String(32))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_from: Mapped[date] = mapped_column(Date)  # IST
    window_to: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8), server_default="running")
    rows: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(500))


FACT_TABLES = (
    FactHousehold,
    FactCycle,
    FactOrder,
    FactIssue,
    FactRoute,
    FactHubDay,
    FactPartnerWeek,
    FactAssignmentRun,
    FactCommunity,
)
