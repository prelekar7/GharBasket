"""The console's side of M11 (spec §3.2): versioned templates with a preview, and the delivery log.

Saving a template never edits a row: it adds the next version in all three locales and makes it
the active one, so the old copy stays for the log and the audit trail.
"""

import re
import string
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.notifications import service
from gharbasket.modules.notifications.kinds import KINDS, Kind, PlaceholderType
from gharbasket.modules.notifications.models import CHANNELS, LOCALES, Delivery, Template
from gharbasket.modules.notifications.render import ordered_values, render
from gharbasket.modules.notifications.schemas import (
    DeliveryOut,
    KindOut,
    LogEntry,
    PreviewText,
    TemplateIn,
    TemplateOut,
    TemplatesOut,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language
from gharbasket.platform.permissions import Permit, forbidden

WA_TEMPLATE_NAME = re.compile(r"[a-z0-9_]{1,100}")
SAMPLES: dict[PlaceholderType, Any] = {
    "money": 69_000,  # ₹690
    "weekday": "2026-10-13",  # a Tuesday
    "time": "2026-10-12T15:30:00Z",  # 9 PM IST
}
LANGUAGE: dict[str, Language] = {locale: lang for lang, locale in service.LOCALE.items()}


def template_out(row: Template) -> TemplateOut:
    kind = KINDS.get(row.kind)
    return TemplateOut(
        id=row.id,
        kind=row.kind,
        event_type=kind.event_type if kind else row.kind,
        channel=row.channel,
        locale=row.locale,
        version=row.version,
        title=row.title,
        body=row.body,
        wa_template_name=row.wa_template_name,
        active=row.active,
        created_by=row.created_by,
        created_at=row.created_at,
    )


async def templates(
    session: AsyncSession,
    kind: str | None,
    channel: str | None,
    locale: str | None,
    history: bool,
) -> TemplatesOut:
    stmt = select(Template)
    for column, value in ((Template.kind, kind), (Template.channel, channel)):
        if value is not None:
            stmt = stmt.where(column == value)
    if locale is not None:
        stmt = stmt.where(Template.locale == locale)
    if not history:
        stmt = stmt.where(Template.active)
    order = (Template.kind, Template.channel, Template.locale, Template.version.desc())
    rows = (await session.execute(stmt.order_by(*order))).scalars()
    kinds = [
        KindOut(
            kind=k, event_type=v.event_type, category=v.category, placeholders=[*v.placeholders]
        )
        for k, v in KINDS.items()
    ]
    return TemplatesOut(kinds=kinds, items=[template_out(r) for r in rows])


def _text_problems(text: str, kind: Kind | None) -> list[str]:
    try:
        fields = list(string.Formatter().parse(text))
    except ValueError:
        return ["a { or } without its pair"]
    problems: list[str] = []
    for _, name, spec, conversion in fields:
        if name is None:
            continue
        if spec or conversion:
            problems.append(f"{{{name}}} has a format spec; values are formatted by their type")
        elif kind is not None and name not in kind.placeholders:
            offered = ", ".join(f"{{{p}}}" for p in kind.placeholders) or "none"
            problems.append(f"{{{name}}} is not a placeholder of this kind (offered: {offered})")
    return problems


def check(body: TemplateIn) -> Kind:
    """The kind of a valid template; else 422 `notifications.template_invalid` with every reason."""
    errors: list[dict[str, Any]] = []

    def problem(loc: list[str | int], message: str) -> None:
        errors.append({"loc": ["body", *loc], "message": message, "type": "template_invalid"})

    kind = KINDS.get(body.kind)
    if kind is None:
        problem(["kind"], f"unknown kind {body.kind!r}")
    if body.channel not in CHANNELS:
        problem(["channel"], f"the channel is one of {', '.join(CHANNELS)}")
    if sorted(t.locale for t in body.texts) != sorted(LOCALES):
        problem(["texts"], f"one text in each of {', '.join(LOCALES)}")
    name = body.wa_template_name
    if body.channel == "whatsapp" and (name is None or not WA_TEMPLATE_NAME.fullmatch(name)):
        problem(["wa_template_name"], "Meta's approved template name ([a-z0-9_]) is needed")
    if body.channel == "app" and name is not None:
        problem(["wa_template_name"], "only WhatsApp templates name one")
    for i, text in enumerate(body.texts):
        if body.channel == "app" and not text.title.strip():
            problem(["texts", i, "title"], "a push needs a title")
        for field in ("title", "body"):
            for message in _text_problems(getattr(text, field), kind):
                problem(["texts", i, field], message)
    if errors or kind is None:
        raise ProblemError(
            422, "notifications.template_invalid", "The template cannot be used.", errors=errors
        )
    return kind


def preview(body: TemplateIn) -> list[PreviewText]:
    """Every locale rendered with sample values; nothing is saved."""
    kind = check(body)
    params = {name: SAMPLES[type_] for name, type_ in kind.placeholders.items()}
    return [
        PreviewText(
            locale=t.locale,
            title=render(t.title, kind, params, LANGUAGE[t.locale]),
            body=render(t.body, kind, params, LANGUAGE[t.locale]),
            params=ordered_values(t.body, kind, params, LANGUAGE[t.locale]),
        )
        for t in sorted(body.texts, key=lambda t: LOCALES.index(t.locale))
    ]


def _snapshot(rows: list[Template]) -> dict[str, Any]:
    return {
        r.locale: {"version": r.version, "title": r.title, "body": r.body, "wa": r.wa_template_name}
        for r in rows
    }


async def save(session: AsyncSession, body: TemplateIn, actor: uuid.UUID) -> list[TemplateOut]:
    """The next version in all three locales, made active; audit-logged."""
    check(body)
    same = (Template.kind == body.kind, Template.channel == body.channel)
    before = list((await session.execute(select(Template).where(*same, Template.active))).scalars())
    version = (await session.scalar(select(func.max(Template.version)).where(*same)) or 0) + 1
    await session.execute(update(Template).where(*same, Template.active).values(active=False))
    rows = [
        Template(
            kind=body.kind,
            channel=body.channel,
            locale=t.locale,
            version=version,
            title=t.title,
            body=t.body,
            wa_template_name=body.wa_template_name,
            active=True,
            created_by=actor,
        )
        for t in sorted(body.texts, key=lambda t: LOCALES.index(t.locale))
    ]
    session.add_all(rows)
    await session.flush()
    for row in rows:
        await session.refresh(row)
    await audit.record(
        session,
        actor_user_id=actor,
        action="notifications.template_saved",
        entity_type="notification_template",
        entity_id=f"{body.kind}/{body.channel}",
        before=_snapshot(before) or None,
        after=_snapshot(rows),
    )
    out = [template_out(r) for r in rows]
    await session.commit()
    return out


async def delivery_log(
    session: AsyncSession,
    permit: Permit,
    user_id: uuid.UUID,
    lang: Language,
    cursor: str | None,
    limit: int,
) -> tuple[list[LogEntry], str | None]:
    """A person's messages with their delivery attempts. A hub manager sees only the people
    whose household their hub serves."""
    if not any(grant.scope_id is None for grant in permit.grants):
        hub = await identity_api.household_hub(session, user_id)
        if hub is None or not permit.allows(hub):
            raise forbidden()
    page, next_cursor = await service.messages_page(session, user_id, cursor, limit)
    shown = await service.active_templates(session, {m.kind for m in page}, lang)
    deliveries: dict[uuid.UUID, list[DeliveryOut]] = {m.id: [] for m in page}
    rows = await session.execute(
        select(Delivery).where(Delivery.message_id.in_(deliveries)).order_by(Delivery.channel)
    )
    for d in rows.scalars():
        deliveries[d.message_id].append(DeliveryOut.model_validate(d, from_attributes=True))
    items = [
        LogEntry(
            **service.rendered(m, shown, lang).model_dump(),
            event_id=m.event_id,
            kind=m.kind,
            deliveries=deliveries[m.id],
        )
        for m in page
    ]
    return items, next_cursor
