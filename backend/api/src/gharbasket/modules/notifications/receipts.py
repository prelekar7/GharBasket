"""WhatsApp's webhook (spec §3.2): status updates for the messages we sent.

Meta delivers each update at least once, so each is stored once under `<wamid>:<status>` and a
repeat changes nothing. An update moves its delivery forward only (sent → delivered → read); a
failure counts until the message was delivered. Incoming customer messages are not stored.
"""

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.notifications.models import Delivery, WebhookEvent

RANK = {"sent": 0, "delivered": 1, "read": 2}


def _dicts(value: Any) -> list[dict[str, Any]]:
    """The objects in a JSON list; anything else is nothing."""
    items = cast(list[Any], value) if isinstance(value, list) else []
    return [cast(dict[str, Any], v) for v in items if isinstance(v, dict)]


def statuses(event: dict[str, Any]) -> list[dict[str, Any]]:
    """The status updates in a webhook body (entry[].changes[].value.statuses[])."""
    if event.get("object") != "whatsapp_business_account":
        return []
    found: list[dict[str, Any]] = []
    for entry in _dicts(event.get("entry")):
        for change in _dicts(entry.get("changes")):
            value = change.get("value")
            if isinstance(value, dict):
                found += [
                    s
                    for s in _dicts(cast(dict[str, Any], value).get("statuses"))
                    if isinstance(s.get("id"), str) and isinstance(s.get("status"), str)
                ]
    return found


async def _apply(session: AsyncSession, status: dict[str, Any]) -> str:
    delivery = await session.scalar(
        select(Delivery)
        .where(Delivery.channel == "whatsapp", Delivery.provider_ref == status["id"])
        .with_for_update()
    )
    if delivery is None:
        return "unknown_delivery"
    new, current = status["status"], delivery.status
    if new == "failed" and current == "sent":
        error = next(iter(_dicts(status.get("errors"))), {})
        reason = f"WhatsApp {error.get('code', '')} {error.get('title', '')}".strip()
        delivery.status, delivery.last_error = "failed", reason[:300]
        return "applied"
    if current in RANK and RANK.get(new, -1) > RANK[current]:
        delivery.status = new
        return "applied"
    return "unchanged"


async def receive(session: AsyncSession, event: dict[str, Any]) -> list[str]:
    """Store and apply each status update once; returns what became of each."""
    outcomes: list[str] = []
    for status in statuses(event):
        stored = await session.scalar(
            insert(WebhookEvent)
            .values(
                provider="whatsapp",
                provider_event_id=f"{status['id']}:{status['status']}"[:200],
                payload=status,
            )
            .on_conflict_do_nothing(index_elements=["provider_event_id"])
            .returning(WebhookEvent.id)
        )
        if stored is None:
            outcomes.append("duplicate")
            continue
        outcome = await _apply(session, status)
        await session.execute(
            update(WebhookEvent).where(WebhookEvent.id == stored).values(outcome=outcome)
        )
        outcomes.append(outcome)
    await session.commit()
    return outcomes
