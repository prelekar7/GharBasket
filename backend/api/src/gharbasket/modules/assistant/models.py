"""Tables of M13 Assistant (Ghar Saathi), in the `assistant` schema (spec §4).

A session is one conversation of one user; messages are kept with personal data scrubbed;
a proposal is a change the model suggested that the person confirms, rejects or lets expire;
usage_daily is the token cap's ledger.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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

SCHEMA = "assistant"
ROLES = ("user", "assistant")
ACTIONS = ("edit_box_line", "skip_week")
PROPOSAL_STATUSES = ("pending", "confirmed", "rejected", "expired")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id", "created_at"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    locale: Mapped[str] = mapped_column(String(10))
    model: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_one_of("role", ROLES), name="role"),
        Index("ix_messages_session", "session_id", "id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)  # PII-scrubbed
    tool_calls: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'"))
    tokens_in: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(_one_of("action", ACTIONS), name="action"),
        CheckConstraint(_one_of("status", PROPOSAL_STATUSES), name="status"),
        Index("ix_proposals_pending", "status", "expires_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    household_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = ({"schema": SCHEMA},)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)  # IST
    tokens_in: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    turns: Mapped[int] = mapped_column(Integer, server_default=text("0"))
