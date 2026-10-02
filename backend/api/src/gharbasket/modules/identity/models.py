"""Tables of M1 Identity & Household. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Login part (this file, so far): users, their roles, sessions (one per device or browser) and the
refresh tokens of each session. Households, members, addresses and consents follow in later PRs.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
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
)
ROLE_STATUSES = ("applied", "kyc_pending", "verified", "suspended")
SCOPE_TYPES = ("society", "hub", "ngo")
LOCALES = ("en-IN", "hi-IN", "mr-IN")
PLATFORMS = ("android", "web")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_e164"),
        CheckConstraint(_one_of("locale", LOCALES), name="locale"),
        CheckConstraint(_one_of("status", ("active", "blocked", "deleted")), name="status"),
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
