"""Tables of M1 Identity & Household. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Login: users, their roles, sessions (one per device or browser) and the refresh tokens of each
session. Household (spec §3.2): one household per owner in v1, its members, its one address and the
owner's consents.
"""

import uuid
from datetime import date, datetime
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
    LargeBinary,
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

SCHEMA = "identity"

ROLES = (
    "customer",
    "captain",
    "kitchen_partner",
    "rider",
    "packer",
    "hub_manager",
    "franchise_owner",
    "ngo_coordinator",
    "admin",
    # Curates recipes: names and the fasting and diabetic flags (M2 §3.3).
    "catalog_editor",
)
# `deleting` during the 30-day grace of an account deletion (spec §3.5); a purged user has no row.
USER_STATUSES = ("active", "blocked", "deleting")
ROLE_STATUSES = ("applied", "kyc_pending", "verified", "suspended")
SCOPE_TYPES = ("society", "hub", "ngo")
LOCALES = ("en-IN", "hi-IN", "mr-IN")
PLATFORMS = ("android", "web")
DIETS = ("veg", "egg", "nonveg")
AGE_BANDS = ("under_5", "5_17", "18_59", "60_plus")
SEXES = ("f", "m", "other")
# What packing does with a short item (spec §3.4): a similar item, or never substitute and refund.
SHORTAGE_PREFERENCES = ("substitute", "refund")
# Handbook 13 §DPDP. The first two are required for Autopilot; the rest are opt-ins.
CONSENT_PURPOSES = (
    "service_delivery",
    "personalisation",
    "health_personalisation",
    "marketing",
    "research_anonymised",
    "whatsapp",  # updates on WhatsApp (M11 §3.1)
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_e164"),
        CheckConstraint(_one_of("locale", LOCALES), name="locale"),
        CheckConstraint(_one_of("status", USER_STATUSES), name="status"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str | None] = mapped_column(String(80))
    locale: Mapped[str] = mapped_column(String(5), server_default="en-IN")
    senior_mode: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    # Simulator rows are "synthetic" so they can be filtered out of real metrics (ADR-0008).
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserRole(Base):
    """A role, optionally limited to one society, hub or NGO ("captain of Sunrise Towers")."""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "role", "scope_type", "scope_id", postgresql_nulls_not_distinct=True
        ),
        CheckConstraint(_one_of("role", ROLES), name="role"),
        CheckConstraint(_one_of("status", ROLE_STATUSES), name="status"),
        CheckConstraint(
            f"(scope_type IS NULL AND scope_id IS NULL) OR ({_one_of('scope_type', SCOPE_TYPES)}"
            " AND scope_id IS NOT NULL)",
            name="scope",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    scope_type: Mapped[str | None] = mapped_column(String(16))
    scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16), server_default="verified")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def grant(self) -> str:
        """The form used in access tokens and X-Active-Role: "customer" or "captain:<id>"."""
        return f"{self.role}:{self.scope_id}" if self.scope_id else self.role


class LoginSession(Base):
    """One signed-in device or browser. Revoking it signs that device out."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(_one_of("platform", PLATFORMS), name="platform"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(16))
    push_token: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # When the OTP behind this session was entered (the Firebase token's auth_time; spec §3.5).
    # Refreshes keep it: only a new OTP moves it, so DELETE /v1/me can ask for a fresh one.
    auth_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Absolute limit: refreshing never extends a session past this (30 days after sign-in).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))


class RefreshToken(Base):
    """One refresh token in a session's chain; each refresh uses one and issues its child."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_parent_id", "parent_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sessions.id", ondelete="CASCADE"), index=True
    )
    # HMAC-SHA256(REFRESH_TOKEN_PEPPER, token): the token itself is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.refresh_tokens.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Household(Base):
    """What Autopilot plans for. `size` is always the number of members."""

    __tablename__ = "households"
    __table_args__ = (
        CheckConstraint(_one_of("diet", DIETS), name="diet"),
        CheckConstraint("size BETWEEN 1 AND 12", name="size"),
        CheckConstraint("weekday_cook_minutes BETWEEN 10 AND 240", name="weekday_cook_minutes"),
        CheckConstraint("weekend_cook_minutes BETWEEN 10 AND 240", name="weekend_cook_minutes"),
        CheckConstraint(
            "no_cook_days <@ ARRAY[0, 1, 2, 3, 4, 5, 6]::smallint[]", name="no_cook_days"
        ),
        CheckConstraint("budget_paise IS NULL OR budget_paise >= 50000", name="budget_paise"),
        CheckConstraint(
            _one_of("shortage_preference", SHORTAGE_PREFERENCES), name="shortage_preference"
        ),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        CheckConstraint("version >= 1", name="version"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # One household per owner in v1.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), unique=True
    )
    society_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("community.societies.id", ondelete="RESTRICT"), index=True
    )
    # Copied from the society; no foreign key: hub ids are checked through hub_ops.api (ADR-0026).
    hub_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    size: Mapped[int] = mapped_column(SmallInteger)
    diet: Mapped[str] = mapped_column(String(8))
    cuisine_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    weekday_cook_minutes: Mapped[int] = mapped_column(SmallInteger)
    weekend_cook_minutes: Mapped[int] = mapped_column(SmallInteger)
    # 0 = Monday ... 6 = Sunday (Python's date.weekday()).
    no_cook_days: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), server_default=text("'{}'")
    )
    # The monthly Budget Lock in paise; null = off.
    budget_paise: Mapped[int | None] = mapped_column(BigInteger)
    autopilot_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    auto_approve: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Spec §3.4: read by M6 packing and M4's mark_shortage when a shortage is recorded.
    shortage_preference: Mapped[str] = mapped_column(Text, server_default="substitute")
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    # Optimistic locking: sent as the ETag, checked against If-Match.
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HouseholdMember(Base):
    __tablename__ = "household_members"
    __table_args__ = (
        CheckConstraint(_one_of("age_band", AGE_BANDS), name="age_band"),
        CheckConstraint(f"sex IS NULL OR {_one_of('sex', SEXES)}", name="sex"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.households.id", ondelete="CASCADE"), index=True
    )
    age_band: Mapped[str] = mapped_column(String(8))
    sex: Mapped[str | None] = mapped_column(String(8))
    # Sensitive (DPDP): stored only while the owner's health_personalisation consent is active.
    health_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    # {"jain": bool, "fasts": [...], "no_onion_garlic_on_fast_days": bool}
    fasting_rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Address(Base):
    """Where the household lives: society, tower and flat (one per household in v1)."""

    __tablename__ = "addresses"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.households.id", ondelete="CASCADE"), unique=True
    )
    society_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("community.societies.id", ondelete="RESTRICT"), index=True
    )
    tower_id: Mapped[str] = mapped_column(String(20))
    flat: Mapped[str] = mapped_column(String(20))
    # Null when no pin was placed. Reads give WKBElement; writes may pass WKTElement.
    location: Mapped[WKBElement | WKTElement | None] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False)
    )
    # H3 cell at resolution 9 (~0.1 km²) for grouping nearby flats; never returned by the API.
    h3_r9: Mapped[str | None] = mapped_column(String(15))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Consent(Base):
    """One grant of one purpose; revoking sets `revoked_at`, granting again adds a new row."""

    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint(_one_of("purpose", CONSENT_PURPOSES), name="purpose"),
        # At most one active grant per purpose.
        Index(
            "uq_consents_active",
            "user_id",
            "purpose",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(32))
    # The notice version the user agreed to ("v1").
    version: Mapped[str] = mapped_column(String(10))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


EXPORT_STATUSES = ("queued", "building", "ready", "failed", "expired")


class DataExport(Base):
    """One "download my data" request (spec §3.3): at most one per user per IST day."""

    __tablename__ = "data_exports"
    __table_args__ = (
        UniqueConstraint("user_id", "requested_on"),
        CheckConstraint(_one_of("status", EXPORT_STATUSES), name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        # build_data_export takes queued rows oldest first.
        Index("ix_data_exports_queued", "requested_at", postgresql_where=text("status = 'queued'")),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"))
    requested_on: Mapped[date] = mapped_column(Date)  # the IST day: the request's natural key
    status: Mapped[str] = mapped_column(String(8), server_default="queued")
    # The JSON file in the private bucket (M0 purpose `data_export`), once built.
    object_key: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(String(500))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Deletion(Base):
    """One account deletion (spec §3.5): asked for, then cancelled or purged after 30 days.

    After the purge the row is the tombstone: the user id, the dates and the rows erased per
    module and table. It never holds a phone, a name or household fields."""

    __tablename__ = "deletions"
    __table_args__ = (
        # purge_deleted_users takes the due, open deletions.
        Index(
            "ix_deletions_due",
            "purge_on",
            postgresql_where=text("cancelled_at IS NULL AND purged_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    # No foreign key: the tombstone outlives the user.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purge_on: Mapped[date] = mapped_column(Date)  # the IST date of the request + 30 days
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The IST day of the purge job's last attempt: a nightly run tries untried rows first and
    # never the same row twice, so blocked or failing accounts cannot starve newer ones.
    last_attempt_on: Mapped[date | None] = mapped_column(Date)
    # Why the last purge attempt waited ("open_orders", …); empty once purged.
    blocked_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {module: {table: rows}}
