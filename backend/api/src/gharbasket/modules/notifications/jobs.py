"""The send pipeline of M11 (spec §6, ADR-0021): events become messages, messages go out.

- **Consumers** (one per mapped event, `routes.py`) write the message and its deliveries, nothing
  else. Every consumer of an event shares one savepoint in the outbox relay, so an exception here
  would replay the other modules' consumers too: a payload we cannot read is logged and dropped,
  and there are no network calls.
- **`send_deliveries`** (every minute) sends what is due: pushes through FCM, retried after 1, 5
  and 30 minutes and then `failed`; a token FCM no longer knows is cleared in M1. WhatsApp
  deliveries are `skipped` until its client arrives (B5).
- **`purge_notifications`** (daily) deletes messages after 180 days, with their deliveries.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx2
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.notifications import routes, service
from gharbasket.modules.notifications.fcm import Failed, FcmClient, Retry, Sent, Unregistered
from gharbasket.modules.notifications.models import Delivery, Message, Template
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.locale import Language
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe

BATCH = 200
RETRY_AFTER = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30))
RETENTION = timedelta(days=180)
ERROR_LENGTH = 300
LANGUAGE: dict[str, Language] = {locale: lang for lang, locale in service.LOCALE.items()}
log = structlog.get_logger("gharbasket.notifications")


async def on_event(session: AsyncSession, event: OutboxEvent) -> None:
    """Any mapped event: the message for its recipient, with a delivery per channel."""
    route_fn = routes.ROUTES.get(event.event_type)
    if route_fn is None:
        return
    try:
        if not isinstance(event.payload, dict):
            raise TypeError("payload is not an object")
        route = await route_fn(session, event.payload)
    except (KeyError, ValueError, TypeError) as exc:
        # Never raise for a payload: the whole event (other modules' consumers too) would replay.
        log.warning(
            "notification_event_unreadable",
            event_type=event.event_type,
            event_id=str(event.id),
            error=type(exc).__name__,
        )
        return
    if route is not None:
        await service.record(session, event, route)


for _event_type in routes.ROUTES:
    subscribe(_event_type, on_event)


# --- sending -----------------------------------------------------------------------------------


@asynccontextmanager
async def _http(given: httpx2.AsyncClient | None) -> AsyncIterator[httpx2.AsyncClient]:
    """The app's shared client, or a short-lived one (tools) that is closed afterwards."""
    if given is not None:
        yield given
        return
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(5.0)) as client:
        yield client


def _finish(delivery: Delivery, status: str, error: str | None = None) -> str:
    delivery.status, delivery.last_error = status, error and error[:ERROR_LENGTH]
    return status


async def _push(
    session: AsyncSession,
    fcm: FcmClient | None,
    delivery: Delivery,
    message: Message,
    templates: dict[tuple[str, Language], dict[tuple[str, str], Template]],
) -> str:
    """One push delivery to every device of the person; returns what became of it."""
    if fcm is None:
        return _finish(delivery, "skipped", "FCM is not set up (FCM_SERVICE_ACCOUNT)")
    devices = await identity_api.active_push_tokens(session, message.user_id)
    if not devices:
        return _finish(delivery, "skipped", "no signed-in device with a push token")
    lang = LANGUAGE.get(await identity_api.user_locale(session, message.user_id), "en")
    if (message.kind, lang) not in templates:
        templates[message.kind, lang] = await service.active_templates(
            session, {message.kind}, lang
        )
    shown = service.rendered(message, templates[message.kind, lang], lang)
    if not shown.title:
        return _finish(delivery, "failed", f"no active template for {message.kind}")

    data = {"link": message.link or "", "message_id": str(message.id)}
    outcomes = [await fcm.send(d.token, shown.title, shown.body, data) for d in devices]
    for device, outcome in zip(devices, outcomes, strict=True):
        if isinstance(outcome, Unregistered):
            await identity_api.clear_push_token(session, device.token)
    delivery.attempts += 1
    sent = [o for o in outcomes if isinstance(o, Sent)]
    if sent:
        delivery.provider_ref = sent[0].name[:200]
        delivery.sent_at = service.now()
        return _finish(delivery, "sent")
    if all(isinstance(o, Unregistered) for o in outcomes):
        return _finish(delivery, "skipped", "every device's token was unregistered")
    reasons = "; ".join(sorted({o.reason for o in outcomes if isinstance(o, Retry | Failed)}))
    if any(isinstance(o, Retry) for o in outcomes) and delivery.attempts <= len(RETRY_AFTER):
        delivery.next_attempt_at = service.now() + RETRY_AFTER[delivery.attempts - 1]
        _finish(delivery, "queued", reasons)
        return "retrying"
    return _finish(delivery, "failed", reasons)


@job("send_deliveries", "Send due pushes (FCM); retry after 1, 5 and 30 minutes, then fail")
async def send_deliveries(ctx: JobContext) -> dict[str, Any]:
    counts = {"sent": 0, "retrying": 0, "failed": 0, "skipped": 0}
    settings = ctx.settings
    async with _http(ctx.http) as http:
        fcm = FcmClient(http, settings.fcm_credentials) if settings.fcm_enabled else None
        templates: dict[tuple[str, Language], dict[tuple[str, str], Template]] = {}
        while True:
            # Every row taken leaves the due set (sent, failed, skipped or due later), so this ends.
            rows = (
                await ctx.session.execute(
                    select(Delivery, Message)
                    .join(Message, Message.id == Delivery.message_id)
                    .where(
                        Delivery.status.in_(("queued", "held")),
                        Delivery.next_attempt_at <= service.now(),
                    )
                    .order_by(Delivery.next_attempt_at)
                    .limit(BATCH)
                    .with_for_update(of=Delivery, skip_locked=True)
                )
            ).all()
            if not rows:
                break
            for delivery, message in rows:
                if delivery.channel == "whatsapp":
                    counts[_finish(delivery, "skipped", "WhatsApp is not set up yet")] += 1
                else:
                    counts[await _push(ctx.session, fcm, delivery, message, templates)] += 1
            await ctx.session.commit()
    return counts


@job("purge_notifications", "Delete messages older than 180 days, with their deliveries")
async def purge_notifications(ctx: JobContext) -> dict[str, Any]:
    deleted = await ctx.session.execute(
        delete(Message)
        .where(Message.created_at < ctx.scheduled_for - RETENTION)
        .returning(Message.id)
    )
    return {"deleted": len(deleted.all())}
