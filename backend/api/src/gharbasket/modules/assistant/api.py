"""Facade of M13 Assistant (Ghar Saathi): the only module other modules may import.

M1 puts `export_for_user` into the data export (M1 §3.3) and calls `delete_for_user` first in
the purge of a deleted account (M1 §3.5, matrix row 1).
"""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, delete, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.assistant.models import Message, Proposal, Session, UsageDaily

__all__ = ["delete_for_user", "export_for_user"]

PRIVATE = frozenset({"user_id", "household_id", "session_id"})


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M13's section of the export, read-only: her conversations, each with its messages (the
    scrubbed text as stored) and proposal cards, and her daily usage; no user or household ids."""
    chats = (
        await session.scalars(
            select(Session).where(Session.user_id == user_id).order_by(Session.created_at)
        )
    ).all()
    ids = [chat.id for chat in chats]
    messages = (
        await session.scalars(
            select(Message).where(Message.session_id.in_(ids)).order_by(Message.id)
        )
    ).all()
    proposals = (
        await session.scalars(
            select(Proposal).where(Proposal.session_id.in_(ids)).order_by(Proposal.id)
        )
    ).all()
    usage = await session.scalars(
        select(UsageDaily).where(UsageDaily.user_id == user_id).order_by(UsageDaily.day)
    )
    return {
        "sessions": [
            {
                **chat.as_dict(exclude=PRIVATE),
                "messages": [
                    m.as_dict(exclude=PRIVATE) for m in messages if m.session_id == chat.id
                ],
                "proposals": [
                    p.as_dict(exclude=PRIVATE) for p in proposals if p.session_id == chat.id
                ],
            }
            for chat in chats
        ],
        "usage_daily": [day.as_dict(exclude=PRIVATE) for day in usage],
    }


async def delete_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 1): the user's conversations and any of her household, with
    their messages and proposals, and her daily usage. Nothing is kept; a rerun deletes nothing."""
    household: ColumnElement[bool] = (
        false() if household_id is None else Session.household_id == household_id
    )
    chats = select(Session.id).where(or_(Session.user_id == user_id, household))
    counts: dict[str, int] = {}
    for model, where in (
        (Proposal, Proposal.session_id.in_(chats)),
        (Message, Message.session_id.in_(chats)),
        (Session, Session.id.in_(chats)),
        (UsageDaily, UsageDaily.user_id == user_id),
    ):
        gone = await session.execute(delete(model).where(where))
        counts[model.__tablename__] = gone.rowcount  # pyright: ignore[reportAttributeAccessIssue]
    return counts
