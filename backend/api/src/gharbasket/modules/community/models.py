"""Tables of M9 Community & Kindness. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Societies and residents' requests for new ones (spec §3.1) with how they unlock (§3.6), captain
appointments (§3.2), the Kindness point rates and donation drives (§3.4), referral codes and
referrals (§3.3), and NGOs with their surplus offers, donated-meal pledges and hand-offs (§3.5).
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
THRESHOLD_SOURCES = ("default", "model", "hub")
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
        CheckConstraint(_one_of("threshold_source", THRESHOLD_SOURCES), name="threshold_source"),
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
    # Pinned by the hub: the weekly recompute leaves the threshold alone.
    unlock_threshold_pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    threshold_source: Mapped[str] = mapped_column(String(8), server_default="default")
    threshold_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Set when a hub manager matches the request to a society: the society it became (accepted)
    # or the one it repeats (duplicate), read by status (§3.9).
    society_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
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
    # The NGO that receives the items (§3.5).
    ngo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.ngos.id"))
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
    # The NGO hand-off that took it (§3.5).
    handoff_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.ngo_handoffs.id"), index=True
    )
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


# --- Referrals (§3.3) ---------------------------------------------------------------------------

REFERRAL_STATUSES = ("joined", "converted", "void")
VOID_REASONS = ("expired", "deletion")


class ReferralCode(Base):
    """A customer's share code (§3.3): random, never derived from a name or phone."""

    __tablename__ = "referral_codes"
    __table_args__ = ({"schema": SCHEMA},)

    # identity.users.id; no foreign key across module schemas.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(6), unique=True)  # upper case
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Referral(Base):
    """A neighbour who claimed a code (§3.3). Rows are kept after erasure, so the impact numbers
    stay true; the user ids then lead to no one."""

    __tablename__ = "referrals"
    __table_args__ = (
        CheckConstraint(_one_of("status", REFERRAL_STATUSES), name="status"),
        CheckConstraint(
            f"void_reason IS NULL OR {_one_of('void_reason', VOID_REASONS)}", name="void_reason"
        ),
        CheckConstraint("(status = 'converted') = (converted_on IS NOT NULL)", name="converted"),
        CheckConstraint("referrer_points >= 0 AND referee_points >= 0", name="points"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        Index("ix_referrals_referrer_converted_on", "referrer_user_id", "converted_on"),
        Index("ix_referrals_referee_household_id", "referee_household_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    code: Mapped[str] = mapped_column(String(6))
    referrer_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # A user is referred at most once.
    referee_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    referee_household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # The referee's society and hub.
    society_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(10), server_default="joined")
    joined_on: Mapped[date] = mapped_column(Date)  # IST
    converted_on: Mapped[date | None] = mapped_column(Date)  # IST
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # the converting box (M4)
    # Promised at the claim (REFERRAL_POINTS_*), paid at conversion; the referrer's becomes 0
    # over the monthly cap.
    referrer_points: Mapped[int] = mapped_column(Integer)
    referee_points: Mapped[int] = mapped_column(Integer)
    # M5 journal entries of the two credits.
    referrer_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    referee_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    void_reason: Mapped[str | None] = mapped_column(String(10))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- NGOs, surplus offers, donated meals and hand-offs (§3.5) ------------------------------------

NGO_KINDS = ("food_bank", "shelter", "school", "other")
NGO_STATUSES = ("active", "paused")
GIFTS = ("items", "food", "meals")  # what an NGO accepts, and the kinds of hand-off
OFFER_STATUSES = ("open", "claimed", "expired")
RECORDED_BY = ("hub", "ngo")


class Ngo(Base):
    """A partner NGO: the hubs it collects from and what it accepts. Its coordinators are M1
    roles `ngo_coordinator:<id>`, not columns."""

    __tablename__ = "ngos"
    __table_args__ = (
        CheckConstraint(_one_of("kind", NGO_KINDS), name="kind"),
        CheckConstraint(_one_of("status", NGO_STATUSES), name="status"),
        CheckConstraint(
            f"cardinality(accepts) > 0 AND accepts <@ ARRAY[{', '.join(repr(g) for g in GIFTS)}]"
            "::text[]",
            name="accepts",
        ),
        CheckConstraint("cardinality(hub_ids) > 0", name="hub_ids"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_ngos_hub_ids", "hub_ids", postgresql_using="gin"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))
    # M6's hub ids, checked through hub_ops.api when written (no foreign key across schemas).
    hub_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    accepts: Mapped[list[str]] = mapped_column(ARRAY(Text))
    registration_ref: Mapped[str | None] = mapped_column(String(60))  # e.g. NGO Darpan's id
    verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(8), server_default="active")
    note: Mapped[str | None] = mapped_column(String(300))  # staff and the NGO's coordinators
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NgoHandoff(Base):
    """Items, food or meals given to an NGO, with photo proof. One the NGO's coordinator records is
    confirmed at once; one the hub records waits for the coordinator. Donations and offers point
    at it (`handoff_id`); `handoff_meals` says which pledges it served."""

    __tablename__ = "ngo_handoffs"
    __table_args__ = (
        CheckConstraint(_one_of("kind", GIFTS), name="kind"),
        CheckConstraint(_one_of("recorded_by", RECORDED_BY), name="recorded_by"),
        CheckConstraint("recorded_by = 'hub' OR confirmed_at IS NOT NULL", name="ngo_confirmed"),
        CheckConstraint("items_count >= 0 AND kg_tenths >= 0 AND meals >= 0", name="summary"),
        CheckConstraint("people_reached IS NULL OR people_reached >= 0", name="people_reached"),
        CheckConstraint("cardinality(photo_keys) > 0", name="photo_keys"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        Index("ix_ngo_handoffs_hub_id_handed_at", "hub_id", "handed_at"),
        Index("ix_ngo_handoffs_ngo_id_handed_at", "ngo_id", "handed_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    ngo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.ngos.id"))
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(8))
    # Pieces, plus kilograms counted as one item per kg (items); the kilograms (items and food);
    # the meals served (meals).
    items_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    kg_tenths: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    meals: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    photo_keys: Mapped[list[str]] = mapped_column(ARRAY(Text))
    people_reached: Mapped[int | None] = mapped_column(Integer)  # as the NGO reports it
    handed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[str] = mapped_column(String(4))
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    note: Mapped[str | None] = mapped_column(String(300))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NgoOffer(Base):
    """M6's surplus offer to an NGO (`hub_ops.surplus_offered`, offered_to ngo), copied when an
    active, verified NGO serves the hub and accepts food. The first claim wins. `open` past
    `expires_at` reads as expired."""

    __tablename__ = "ngo_offers"
    __table_args__ = (
        CheckConstraint(_one_of("status", OFFER_STATUSES), name="status"),
        CheckConstraint(
            "(status = 'claimed') = (claimed_by_ngo_id IS NOT NULL AND claimed_at IS NOT NULL)",
            name="claimed",
        ),
        CheckConstraint("handoff_id IS NULL OR status = 'claimed'", name="handoff"),
        CheckConstraint("qty > 0", name="qty"),
        CheckConstraint("kg_tenths IS NULL OR kg_tenths >= 0", name="kg_tenths"),
        Index("ix_ngo_offers_hub_id_status", "hub_id", "status"),
        {"schema": SCHEMA},
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # M6's surplus offer id
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit: Mapped[str] = mapped_column(String(8))  # M6's operations unit: kg, l, piece, bunch, pack
    # Null when the weight is unknown (a pack, or a piece without the catalog's unit_g).
    kg_tenths: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(8), server_default="open")
    claimed_by_ngo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.ngos.id"))
    claimed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handoff_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.ngo_handoffs.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MealPledge(Base):
    """Meals a customer donated with Kindness points (`billing.points_donated`), at her household's
    hub; a `meals` hand-off serves them oldest first. Kept after erasure: no personal fields."""

    __tablename__ = "meal_pledges"
    __table_args__ = (
        CheckConstraint("meals > 0", name="meals"),
        CheckConstraint("served BETWEEN 0 AND meals", name="served"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        Index(
            "ix_meal_pledges_hub_id_pledged_at",
            "hub_id",
            "pledged_at",
            postgresql_where=text("served < meals"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)  # M5's points_donated entry
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    # The user's household hub; null without one (no hub can serve it).
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    meals: Mapped[int] = mapped_column(Integer)
    served: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    pledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")


class HandoffMeal(Base):
    """Which pledges a `meals` hand-off served, and how many meals of each (a pledge may be
    split across hand-offs)."""

    __tablename__ = "handoff_meals"
    __table_args__ = (CheckConstraint("meals > 0", name="meals"), {"schema": SCHEMA})

    handoff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.ngo_handoffs.id"), primary_key=True
    )
    pledge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.meal_pledges.id"), primary_key=True, index=True
    )
    meals: Mapped[int] = mapped_column(Integer)
