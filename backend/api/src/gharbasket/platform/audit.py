"""Audit trail for admin and money-related actions (M0 spec §4, `platform.audit_log`)."""

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.models import AuditEntry


async def record(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry in the caller's transaction, tagged with the current request id."""
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    session.add(
        AuditEntry(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            request_id=request_id,
        )
    )
    await session.flush()
