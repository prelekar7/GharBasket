"""Tables of M11 Notifications, in the `notifications` schema (spec §4).

A message is one event for one person: it keeps the event's raw values (`params`) and is rendered
from the active template in the reader's language every time it is read. Preferences hold only
the choices someone saved; the defaults (§3.1) fill in the rest.
"""

import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.modules.notifications.kinds import ACCOUNT, CATEGORIES
from gharbasket.platform.db import Base

SCHEMA = "notifications"

CHANNELS = ("app", "whatsapp")  # a template's channel: `app` is the inbox and the push
LOCALES = ("en-IN", "hi-IN", "mr-IN")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint(_one_of("channel", CHANNELS), name="channel"),
        CheckConstraint(_one_of("locale", LOCALES), name="locale"),
        UniqueConstraint("kind", "channel", "locale", "version"),
        # One active version per kind, channel and locale.
        Index(
            "ux_templates_active",
            "kind",
            "channel",
            "locale",
            unique=True,
            postgresql_where=text("active"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    kind: Mapped[str] = mapped_column(String(64))  # a key of kinds.KINDS
    channel: Mapped[str] = mapped_column(String(8))
    locale: Mapped[str] = mapped_column(String(5))
    version: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(String(500))
    wa_template_name: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # null for the seeded copy
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_one_of("category", (*CATEGORIES, ACCOUNT)), name="category"),
        UniqueConstraint("event_id", "user_id"),  # one message per event and person
        Index("ix_messages_user", "user_id", "id"),
        Index("ix_messages_unread", "user_id", postgresql_where=text("read_at IS NULL")),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # the outbox event it came from
    event_type: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(12))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    link: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Preference(Base):
    __tablename__ = "preferences"
    __table_args__ = (
        CheckConstraint(_one_of("category", CATEGORIES), name="category"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    category: Mapped[str] = mapped_column(String(12), primary_key=True)
    push: Mapped[bool] = mapped_column(Boolean)
    whatsapp: Mapped[bool] = mapped_column(Boolean)


class QuietHours(Base):
    __tablename__ = "quiet_hours"
    __table_args__ = (
        CheckConstraint("start_time <> end_time", name="not_empty"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    start_time: Mapped[time] = mapped_column(Time)  # IST clock time
    end_time: Mapped[time] = mapped_column(Time)
