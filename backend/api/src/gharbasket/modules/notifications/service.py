"""Business logic of M11 Notifications: the inbox and preferences (spec §3.1), and the message
and its deliveries that an event leaves behind (§3.1 rules)."""

import re
import uuid
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.notifications.kinds import ACCOUNT, CATEGORIES, KINDS
from gharbasket.modules.notifications.models import (
    Delivery,
    Message,
    Preference,
    QuietHours,
    Template,
)
from gharbasket.modules.notifications.quiet import held_until
from gharbasket.modules.notifications.render import render
from gharbasket.modules.notifications.routes import Route
from gharbasket.modules.notifications.schemas import (
    CategoryPreference,
    NotificationOut,
    PreferencesIn,
    PreferencesOut,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.pagination import decode_cursor, encode_cursor

LOCALE: dict[Language, str] = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}
FALLBACK_LOCALE = "en-IN"
QUIET_DEFAULT = (time(22), time(7))
CLOCK = re.compile(r"([01]\d|2[0-3]):(00|15|30|45)")


def now() -> datetime:
    return datetime.now(UTC)


def default_preference(category: str) -> CategoryPreference:
    """Push on except offers; WhatsApp off until the person asks for it (§3.1)."""
    return CategoryPreference(category=category, push=category != "offers", whatsapp=False)


# --- the inbox ---------------------------------------------------------------------------------


async def active_templates(
    session: AsyncSession, kinds: set[str], lang: Language
) -> dict[tuple[str, str], Template]:
    """The active app templates of these kinds in the reader's locale and the fallback."""
    if not kinds:
        return {}
    rows = await session.execute(
        select(Template).where(
            Template.channel == "app",
            Template.active,
            Template.kind.in_(kinds),
            Template.locale.in_({LOCALE[lang], FALLBACK_LOCALE}),
        )
    )
    return {(t.kind, t.locale): t for t in rows.scalars()}


async def whatsapp_template(session: AsyncSession, kind: str, locale: str) -> Template | None:
    """The active WhatsApp template of this kind in the person's locale, else in English."""
    rows = await session.execute(
        select(Template).where(
            Template.channel == "whatsapp",
            Template.active,
            Template.kind == kind,
            Template.locale.in_({locale, FALLBACK_LOCALE}),
        )
    )
    found = {t.locale: t for t in rows.scalars()}
    return found.get(locale) or found.get(FALLBACK_LOCALE)


def rendered(
    message: Message, templates: dict[tuple[str, str], Template], lang: Language
) -> NotificationOut:
    template = templates.get((message.kind, LOCALE[lang])) or templates.get(
        (message.kind, FALLBACK_LOCALE)
    )
    kind = KINDS.get(message.kind)
    title = body = ""
    if template is not None and kind is not None:
        title = render(template.title, kind, message.params, lang)
        body = render(template.body, kind, message.params, lang)
    return NotificationOut(
        id=message.id,
        event_type=message.event_type,
        category=message.category,
        title=title,
        body=body,
        link=message.link,
        created_at=message.created_at,
        read_at=message.read_at,
    )


async def messages_page(
    session: AsyncSession, user_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[Message], str | None]:
    """Newest first. Message ids are uuidv7 (time-ordered), so the cursor is the last id seen."""
    stmt = select(Message).where(Message.user_id == user_id)
    if cursor is not None:
        try:
            before = uuid.UUID(str(decode_cursor(cursor).get("before")))
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(Message.id < before)
    rows = list(
        (await session.execute(stmt.order_by(Message.id.desc()).limit(limit + 1))).scalars()
    )
    page = rows[:limit]
    return page, encode_cursor({"before": str(page[-1].id)}) if len(rows) > limit else None


async def inbox(
    session: AsyncSession, user_id: uuid.UUID, lang: Language, cursor: str | None, limit: int
) -> tuple[list[NotificationOut], int, str | None]:
    page, next_cursor = await messages_page(session, user_id, cursor, limit)
    templates = await active_templates(session, {m.kind for m in page}, lang)
    unread = await session.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.user_id == user_id, Message.read_at.is_(None))
    )
    return [rendered(m, templates, lang) for m in page], unread or 0, next_cursor


async def mark_read(session: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID) -> None:
    """Keeps the first read time; someone else's message is not found."""
    found = await session.scalar(
        select(Message.id).where(Message.id == message_id, Message.user_id == user_id)
    )
    if found is None:
        raise ProblemError(404, "notifications.not_found", "There is no such message.")
    await session.execute(
        update(Message)
        .where(Message.id == message_id, Message.read_at.is_(None))
        .values(read_at=now())
    )


async def mark_all_read(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(Message)
        .where(Message.user_id == user_id, Message.read_at.is_(None))
        .values(read_at=now())
    )


# --- preferences -------------------------------------------------------------------------------


async def preferences(session: AsyncSession, user_id: uuid.UUID) -> PreferencesOut:
    saved = {
        p.category: CategoryPreference(category=p.category, push=p.push, whatsapp=p.whatsapp)
        for p in (
            await session.execute(select(Preference).where(Preference.user_id == user_id))
        ).scalars()
    }
    quiet = await session.get(QuietHours, user_id)
    start, end = (quiet.start_time, quiet.end_time) if quiet else QUIET_DEFAULT
    return PreferencesOut(
        categories=[saved.get(c) or default_preference(c) for c in CATEGORIES],
        quiet_start=start.strftime("%H:%M"),
        quiet_end=end.strftime("%H:%M"),
    )


def _clock(value: str) -> time:
    if not CLOCK.fullmatch(value):
        raise ProblemError(
            422,
            "notifications.quiet_hours_invalid",
            "Quiet hours are HH:MM in 15-minute steps.",
        )
    return time.fromisoformat(value)


async def whatsapp_allowed(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """The M1 `whatsapp` consent (§3.1), checked when saving and again for every message."""
    return await identity_api.has_consent(session, user_id, "whatsapp")


async def save_preferences(
    session: AsyncSession, user_id: uuid.UUID, body: PreferencesIn
) -> PreferencesOut:
    unknown = [c.category for c in body.categories if c.category not in CATEGORIES]
    if unknown:
        raise ProblemError(
            422,
            "notifications.category_unknown",
            f"Unknown category: {', '.join(unknown)}.",
            extra={"categories": unknown},
        )
    if any(c.whatsapp for c in body.categories) and not await whatsapp_allowed(session, user_id):
        raise ProblemError(
            403,
            "notifications.whatsapp_consent_required",
            "Agree to updates on WhatsApp first.",
        )
    current = await preferences(session, user_id)
    start = _clock(body.quiet_start) if body.quiet_start else _clock(current.quiet_start)
    end = _clock(body.quiet_end) if body.quiet_end else _clock(current.quiet_end)
    if start == end:
        raise ProblemError(
            422, "notifications.quiet_hours_invalid", "Quiet hours must start and end apart."
        )
    for choice in {c.category: c for c in body.categories}.values():  # the last one wins
        values: dict[str, Any] = {"push": choice.push, "whatsapp": choice.whatsapp}
        await session.execute(
            insert(Preference)
            .values(user_id=user_id, category=choice.category, **values)
            .on_conflict_do_update(index_elements=["user_id", "category"], set_=values)
        )
    if (start, end) != (_clock(current.quiet_start), _clock(current.quiet_end)):
        times = {"start_time": start, "end_time": end}
        await session.execute(
            insert(QuietHours)
            .values(user_id=user_id, **times)
            .on_conflict_do_update(index_elements=["user_id"], set_=times)
        )
    return await preferences(session, user_id)


# --- messages from events ----------------------------------------------------------------------


async def channels_for(
    session: AsyncSession, user_id: uuid.UUID, category: str, saved: PreferencesOut
) -> list[str]:
    """Where a message goes besides the inbox. Account messages are always pushed (§3.1)."""
    if category == ACCOUNT:
        return ["push"]
    choice = next(c for c in saved.categories if c.category == category)
    channels = ["push"] if choice.push else []
    if choice.whatsapp and await whatsapp_allowed(session, user_id):
        channels.append("whatsapp")
    return channels


async def record(session: AsyncSession, event: OutboxEvent, route: Route) -> int:
    """The message for each recipient, and a delivery per channel for a new one only, so an event
    delivered twice changes nothing. Returns the number of new messages."""
    kind = KINDS[route.kind]
    created = 0
    for user_id in route.user_ids:
        message_id = await session.scalar(
            insert(Message)
            .values(
                user_id=user_id,
                event_id=event.id,
                event_type=event.event_type,
                kind=route.kind,
                category=kind.category,
                params=route.params,
                link=kind.link,
            )
            .on_conflict_do_nothing(index_elements=["event_id", "user_id"])
            .returning(Message.id)
        )
        if message_id is None:
            continue
        created += 1
        saved = await preferences(session, user_id)
        channels = await channels_for(session, user_id, kind.category, saved)
        at = now()
        quiet = (_clock(saved.quiet_start), _clock(saved.quiet_end))
        held = None if kind.critical else held_until(at, *quiet)
        session.add_all(
            Delivery(
                message_id=message_id,
                channel=channel,
                status="queued" if held is None else "held",
                next_attempt_at=held or at,
            )
            for channel in channels
        )
    await session.flush()
    return created
