"""Facade of M11 Notifications: the only module other modules may import.

- M1's data export reads `export_for_user` (M1 §3.3).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.notifications.models import Message
from gharbasket.modules.notifications.service import active_templates, rendered


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M11's section of the user's data export, read-only: each inbox message in English."""
    messages = list(
        (
            await session.execute(
                select(Message).where(Message.user_id == user_id).order_by(Message.id)
            )
        ).scalars()
    )
    templates = await active_templates(session, {m.kind for m in messages}, "en")
    return {
        "messages": [
            rendered(m, templates, "en").model_dump(mode="json", exclude={"id"}) for m in messages
        ]
    }
