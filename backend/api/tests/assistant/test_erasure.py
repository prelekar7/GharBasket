"""M13's part of M1's data rights (M1 §3.3, §3.5 matrix row 1): the export holds her chats
without ids, and the purge deletes her chats, her household's and her usage, nobody else's."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.assistant import api as assistant_api
from gharbasket.modules.assistant.models import Message, Proposal, Session, UsageDaily

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def chat(db: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID) -> Session:
    """A conversation with one exchange and one card, and the day's usage."""
    found = Session(user_id=user_id, household_id=household_id, locale="hi", model="haiku")
    db.add(found)
    await db.flush()
    db.add_all(
        [
            Message(session_id=found.id, role="user", content="bhindi mat bhejna, [address]"),
            Message(session_id=found.id, role="assistant", content="Theek hai", tokens_out=12),
            Proposal(
                session_id=found.id,
                user_id=user_id,
                household_id=household_id,
                action="skip_week",
                payload={"week": "next"},
                summary="Skip next week",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            ),
            UsageDaily(user_id=user_id, day=date(2026, 11, 10), tokens_in=300, turns=1),
        ]
    )
    await db.flush()
    return found


async def rows_of(db: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    mine = select(Session.id).where(Session.user_id == user_id)
    wheres = {
        "sessions": (Session, Session.user_id == user_id),
        "messages": (Message, Message.session_id.in_(mine)),
        "proposals": (Proposal, Proposal.user_id == user_id),
        "usage_daily": (UsageDaily, UsageDaily.user_id == user_id),
    }
    return {
        name: (await db.execute(select(func.count()).select_from(model).where(where))).scalar_one()
        for name, (model, where) in wheres.items()
    }


async def test_the_export_holds_her_chats_without_ids(db_session: AsyncSession) -> None:
    priya, home = uuid.uuid4(), uuid.uuid4()
    await chat(db_session, priya, home)
    await chat(db_session, uuid.uuid4(), uuid.uuid4())  # someone else's

    section = await assistant_api.export_for_user(db_session, priya)

    (session,) = section["sessions"]
    assert session["locale"] == "hi" and "user_id" not in session and "household_id" not in session
    assert [m["content"] for m in session["messages"]] == [
        "bhindi mat bhejna, [address]",
        "Theek hai",
    ]
    assert all("session_id" not in m for m in session["messages"])
    (card,) = session["proposals"]
    assert card["summary"] == "Skip next week" and "user_id" not in card
    assert section["usage_daily"] == [
        {"day": date(2026, 11, 10), "tokens_in": 300, "tokens_out": 0, "turns": 1}
    ]
    assert await assistant_api.export_for_user(db_session, uuid.uuid4()) == {
        "sessions": [],
        "usage_daily": [],
    }


async def test_the_purge_deletes_her_and_her_households_chats_once(
    db_session: AsyncSession,
) -> None:
    priya, home, meena = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await chat(db_session, priya, home)
    # A chat of the household under another user id is the household's data too.
    other = await chat(db_session, uuid.uuid4(), home)
    await chat(db_session, meena, uuid.uuid4())

    erased = await assistant_api.delete_for_user(db_session, priya, home)
    again = await assistant_api.delete_for_user(db_session, priya, home)

    assert erased == {"proposals": 2, "messages": 4, "sessions": 2, "usage_daily": 1}
    assert again == {"proposals": 0, "messages": 0, "sessions": 0, "usage_daily": 0}
    assert await db_session.get(Session, other.id) is None
    assert set((await rows_of(db_session, priya)).values()) == {0}
    assert await rows_of(db_session, meena) == {
        "sessions": 1,
        "messages": 2,
        "proposals": 1,
        "usage_daily": 1,
    }
    # Without a household, only her own rows go.
    assert await assistant_api.delete_for_user(db_session, meena, None) == {
        "proposals": 1,
        "messages": 2,
        "sessions": 1,
        "usage_daily": 1,
    }
