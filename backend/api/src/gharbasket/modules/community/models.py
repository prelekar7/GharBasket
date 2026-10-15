"""Tables of M9 Community & Kindness. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Societies and residents' requests for new ones (spec §3.1), captain appointments (§3.2), and the
Kindness point rates and donation drives (§3.4). Referrals, donations and NGOs follow in P5.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography, WKBElement, WKTElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "community"

SOCIETY_STATUSES = ("prospect", "unlocking", "live", "paused")
REQUEST_STATUSES = ("received", "accepted", "rejected", "duplicate")

# What society search matches against: name and area, lower-cased and without accents. The
# migration creates `community.f_unaccent`, an IMMUTABLE wrapper (unaccent() itself is only
# STABLE, so it cannot be used in a generated column or an index).
SEARCH_TEXT = "community.f_unaccent(lower(name || ' ' || coalesce(area, '')))"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Society(Base):
    __tablename__ = "societies"
    __table_args__ = (
        CheckConstraint(_one_of("status", SOCIETY_STATUSES), name="status"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        CheckConstraint("unlock_threshold > 0", name="unlock_threshold"),
        CheckConstraint("jsonb_typeof(towers) = 'array'", name="towers"),
        Index("ix_societies_boundary", "boundary", postgresql_using="gist"),
        Index(
            "ix_societies_search_text",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # The hub that serves it; no foreign key: hub ids are checked through hub_ops.api (ADR-0026).
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    area: Mapped[str | None] = mapped_column(String(120))
    # Null until a hub manager draws it; addresses are then not checked against it. Reads give
    # WKBElement; writes may pass WKTElement.
    boundary: Mapped[WKBElement | WKTElement | None] = mapped_column(
        Geography("POLYGON", srid=4326, spatial_index=False)
    )
    # [{"id": "a", "label": "A wing"}]; tower ids are unique within the society.
    towers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    flats: Mapped[int | None] = mapped_column(Integer)
    # Households needed for free delivery (US-2).
    unlock_threshold: Mapped[int] = mapped_column(Integer, server_default=text("20"))
    status: Mapped[str] = mapped_column(String(16), server_default="prospect")
    # Set once, when the society first goes live; never cleared (M9 §3.6). A box pays the hub's
    # pre-live delivery fee while it is null (M4 `box_fee`).
    live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_text: Mapped[str] = mapped_column(Text, Computed(SEARCH_TEXT, persisted=True))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SocietyRequest(Base):
    """A resident asking GharBasket to come to a society that isn't listed (US-8)."""

    __tablename__ = "society_requests"
    __table_args__ = (
        CheckConstraint(_one_of("status", REQUEST_STATUSES), name="status"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # identity.users.id; no foreign key across module schemas.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    name: Mapped[str] = mapped_column(String(120))
    area: Mapped[str | None] = mapped_column(String(120))
    # Null when the resident placed no pin: never a default map centre.
    location: Mapped[WKBElement | WKTElement | None] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False)
    )
    note: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), server_default="received")
    # Set when a hub manager matches the request to a society.
    society_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


CAPTAIN_STATUSES = ("active", "ended")
APPOINTED_BY = ("auto", "hub")
END_REASONS = ("stepped_down", "replaced", "hub_decision", "suspended")
ACTIVE = text("status = 'active'")


class Captain(Base):
    """An appointment: which verified captain serves which society, from when to when (§3.2,
    ADR-0026 §4). At most one active per society, and one active society per captain."""

    __tablename__ = "captains"
    __table_args__ = (
        CheckConstraint(_one_of("status", CAPTAIN_STATUSES), name="status"),
        CheckConstraint(_one_of("appointed_by", APPOINTED_BY), name="appointed_by"),
        CheckConstraint(
            f"end_reason IS NULL OR {_one_of('end_reason', END_REASONS)}", name="end_reason"
        ),
        CheckConstraint(
            "(status = 'ended') = (until IS NOT NULL AND end_reason IS NOT NULL)", name="ended"
        ),
        Index("uq_captains_active_society", "society_id", unique=True, postgresql_where=ACTIVE),
        Index("uq_captains_active_partner", "partner_id", unique=True, postgresql_where=ACTIVE),
        Index("ix_captains_user_id", "user_id"),
        Index("ix_captains_society_id_since", "society_id", "since"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    society_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.societies.id"))
    # Copied from the society at appointment.
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # identity.users.id and partners.partners.id: plain UUIDs, no foreign key across schemas.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    partner_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    appointed_by: Mapped[str] = mapped_column(String(8))
    appointed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    end_reason: Mapped[str | None] = mapped_column(String(16))
    ended_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Staff-only, from the end request.
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Kindness: point rates and donation drives (§3.4) -------------------------------------------

RATE_UNITS = ("kg", "each")
DRIVE_SCOPES = ("platform", "hub", "society")


class PointRate(Base):
    """Kindness points per kg or per piece of one item category; `max_qty` caps one donation."""

    __tablename__ = "point_rates"
    __table_args__ = (
        CheckConstraint(_one_of("unit", RATE_UNITS), name="unit"),
        CheckConstraint("points > 0", name="points"),
        CheckConstraint("max_qty > 0", name="max_qty"),
        {"schema": SCHEMA},
    )

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    label: Mapped[dict[str, str]] = mapped_column(JSONB)  # {en, hi, mr}
    unit: Mapped[str] = mapped_column(String(4))
    points: Mapped[int] = mapped_column(Integer)
    max_qty: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DonationDrive(Base):
    """A time-boxed drive that multiplies the points of some categories. A society drive keeps its
    society's hub in `hub_id`, so hub staff find every drive of their hub by `hub_id`."""

    __tablename__ = "donation_drives"
    __table_args__ = (
        CheckConstraint(_one_of("scope", DRIVE_SCOPES), name="scope"),
        CheckConstraint(
            "(scope = 'platform' AND hub_id IS NULL AND society_id IS NULL)"
            " OR (scope = 'hub' AND hub_id IS NOT NULL AND society_id IS NULL)"
            " OR (scope = 'society' AND hub_id IS NOT NULL AND society_id IS NOT NULL)",
            name="scope_ids",
        ),
        CheckConstraint("multiplier BETWEEN 1.0 AND 3.0", name="multiplier"),
        CheckConstraint("ends_on >= starts_on", name="dates"),
        CheckConstraint("goal_items IS NULL OR goal_items > 0", name="goal_items"),
        CheckConstraint("cardinality(codes) > 0", name="codes"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_donation_drives_hub_id", "hub_id"),
        Index("ix_donation_drives_starts_on_id", "starts_on", "id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[dict[str, str]] = mapped_column(JSONB)  # {en, hi, mr}
    description: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    scope: Mapped[str] = mapped_column(String(8))
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.societies.id"))
    codes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    multiplier: Mapped[Decimal] = mapped_column(Numeric(2, 1))
    starts_on: Mapped[date] = mapped_column(Date)  # IST dates, inclusive
    ends_on: Mapped[date] = mapped_column(Date)
    # The receiving NGO; the foreign key to community.ngos comes with §3.5.
    ngo_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    goal_items: Mapped[int | None] = mapped_column(Integer)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Kindness: donations (§3.4) -----------------------------------------------------------------

DONATION_STATUSES = ("scheduled", "picked_up", "verified", "rejected", "handed_over", "cancelled")
REJECT_REASONS = ("unusable", "not_accepted", "unsafe", "other")
CANCEL_REASONS = ("customer", "not_ready")


class Donation(Base):
    """Items a household gives, collected at the door on a delivery day and counted at the hub.

    `items` is `[{code, unit, qty, counted, rate, points}]`: `qty` as declared, the rest set at
    verification from the rates in force then. `user_id` stays after erasure (like M4's orders).
    """

    __tablename__ = "donations"
    __table_args__ = (
        CheckConstraint(_one_of("status", DONATION_STATUSES), name="status"),
        CheckConstraint(
            f"reject_reason IS NULL OR {_one_of('reject_reason', REJECT_REASONS)}",
            name="reject_reason",
        ),
        CheckConstraint(
            f"cancel_reason IS NULL OR {_one_of('cancel_reason', CANCEL_REASONS)}",
            name="cancel_reason",
        ),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        CheckConstraint("jsonb_typeof(items) = 'array'", name="items"),
        CheckConstraint("pickup_misses BETWEEN 0 AND 1", name="pickup_misses"),
        CheckConstraint("multiplier BETWEEN 1.0 AND 3.0", name="multiplier"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_donations_hub_id_status", "hub_id", "status"),
        Index("ix_donations_household_id_status", "household_id", "status"),
        Index("ix_donations_drive_id", "drive_id"),
        Index(
            "ix_donations_society_id_pickup_on",
            "society_id",
            "pickup_on",
            postgresql_where=text("status = 'scheduled'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # identity's user and household ids, M6's hub id: plain UUIDs, no foreign key across schemas.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    society_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.societies.id"))
    drive_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.donation_drives.id"))
    # The drive's multiplier when it was scheduled: a later change or cancel does not touch it.
    multiplier: Mapped[Decimal] = mapped_column(Numeric(2, 1), server_default=text("1.0"))
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    photo_keys: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(12), server_default="scheduled")
    pickup_on: Mapped[date] = mapped_column(Date)
    pickup_misses: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    picked_up_on: Mapped[date | None] = mapped_column(Date)
    picked_up_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    estimated_points: Mapped[int] = mapped_column(Integer)
    points_awarded: Mapped[int | None] = mapped_column(Integer)
    has_unsuitable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reject_reason: Mapped[str | None] = mapped_column(String(16))
    cancel_reason: Mapped[str | None] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(String(300))  # the hub's, staff only
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The NGO hand-off that took it; the foreign key to community.ngo_handoffs comes with §3.5.
    handoff_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DonationEvent(Base):
    """A donation's timeline: one row per status it reached."""

    __tablename__ = "donation_events"
    __table_args__ = (
        CheckConstraint(_one_of("status", DONATION_STATUSES), name="status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    donation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.donations.id"), index=True)
    status: Mapped[str] = mapped_column(String(12))
    by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
