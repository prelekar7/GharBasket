"""Tables of M9 Community & Kindness. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Societies and residents' requests for new ones (spec §3.1) with how they unlock (§3.6), captain
appointments (§3.2), and referral codes and referrals (§3.3). Donations and NGOs follow.
"""

import uuid
from datetime import date, datetime
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
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    # Set once, when it first went live (§3.6); never cleared, so delivery stays free after a pause.
    live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Pinned by the hub: the weekly recompute leaves the threshold alone.
    unlock_threshold_pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    threshold_source: Mapped[str] = mapped_column(String(8), server_default="default")
    threshold_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
