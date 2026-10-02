"""Transactional outbox: publish an event in the same transaction as the change it describes.

If the transaction rolls back, the event disappears with it; if it commits, the event is guaranteed
to be delivered (at least once) by the relay. Consumers deduplicate on the event id.
"""

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.models import OutboxEvent

# "<module>.<event>", e.g. "orders.locked", "identity.household_created".
_EVENT_TYPE = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")


async def publish(session: AsyncSession, event_type: str, payload: dict[str, Any]) -> uuid.UUID:
    """Add an event to the outbox; it is sent only if the caller's transaction commits."""
    if not _EVENT_TYPE.fullmatch(event_type):
        raise ValueError(f"event type must look like 'module.event', got {event_type!r}")
    event = OutboxEvent(event_type=event_type, payload=payload)
    session.add(event)
    await session.flush()  # assigns the uuidv7 id from the database
    return event.id
