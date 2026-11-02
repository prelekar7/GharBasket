"""`intel.drift_detected` (M10 §6) tells every admin, and only admins, in their own language
(M11 §3.1 event map): an account message, always pushed, opening the console's AI health page."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.notifications.jobs import on_event
from gharbasket.modules.notifications.kinds import ACCOUNT, KINDS
from gharbasket.modules.notifications.models import Delivery, Message
from gharbasket.modules.notifications.render import format_value
from gharbasket.modules.notifications.service import inbox
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from .. import seed_cache as cached
from .conftest import MEENA, PRIYA, Home

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NEHA = "+919800001011"  # the seed's admin; en-IN


def drift(**more: Any) -> dict[str, Any]:
    return {
        "hub_id": str(SEED_HUB_ID),
        "data_source": "synthetic",
        "metric": "wape",
        "live": 0.4123,
        "backtest": 0.25,
        "delta": 0.1623,
        "window_end": "2026-10-09",
    } | more


async def deliver(db: AsyncSession, payload: dict[str, Any]) -> list[Message]:
    event = OutboxEvent(id=uuid.uuid4(), event_type="intel.drift_detected", payload=payload)
    await on_event(db, event)
    rows = await db.execute(select(Message).where(Message.event_id == event.id))
    return list(rows.scalars())


async def make_admin(db: AsyncSession, user_id: uuid.UUID) -> None:
    db.add(UserRole(user_id=user_id, role="admin", status="verified"))
    await db.flush()


def test_the_kind_is_an_account_message_that_opens_ai_health() -> None:
    kind = KINDS["intel.drift_detected"]
    assert (kind.category, kind.link) == (ACCOUNT, "/ai-health")
    assert format_value("percent", 0.4123, "en") == "41%"
    assert format_value("text", "Pune Central", "hi") == "Pune Central"


async def test_every_admin_is_told_in_their_language_and_nobody_else(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    neha = await db_session.scalar(select(User.id).where(User.phone_e164 == NEHA))
    assert neha is not None
    meena, priya = homes[MEENA].user_id, homes[PRIYA].user_id  # Meena: hi-IN, a second admin
    await make_admin(db_session, meena)
    assert set(await identity_api.users_with_role(db_session, "admin")) == {neha, meena}

    await cached.seed_hubs(db_session)
    hub = await hub_ops_api.get_hub(db_session, SEED_HUB_ID)
    assert hub is not None
    messages = await deliver(db_session, drift())

    assert {m.user_id for m in messages} == {neha, meena}  # not Priya, not the hub manager
    assert priya not in {m.user_id for m in messages}
    for message in messages:
        assert (message.kind, message.category, message.link) == (
            "intel.drift_detected",
            ACCOUNT,
            "/ai-health",
        )
        assert message.params == {"hub": hub.name, "live": 0.4123, "backtest": 0.25}
        pushes = await db_session.scalars(select(Delivery).where(Delivery.message_id == message.id))
        assert [d.channel for d in pushes] == ["push"]  # account messages are always pushed


async def test_the_inbox_reads_in_each_admins_locale(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    neha = await db_session.scalar(select(User.id).where(User.phone_e164 == NEHA))
    assert neha is not None
    meena = homes[MEENA].user_id
    await make_admin(db_session, meena)
    await deliver(db_session, drift())

    english, _, _ = await inbox(db_session, neha, "en", None, 10)
    hindi, _, _ = await inbox(db_session, meena, "hi", None, 10)

    assert english[0].title.startswith("Forecast drift at ")
    assert "41%" in english[0].body and "25%" in english[0].body
    assert hindi[0].title.endswith("पर फ़ोरकास्ट में गड़बड़ी")
    assert "41%" in hindi[0].body


async def test_a_blocked_admin_and_a_hub_scoped_role_are_not_told(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    neha = await db_session.scalar(select(User.id).where(User.phone_e164 == NEHA))
    assert neha is not None
    meena = homes[MEENA].user_id
    await make_admin(db_session, meena)
    await db_session.execute(update(User).where(User.id == meena).values(status="blocked"))
    assert await identity_api.users_with_role(db_session, "admin") == [neha]
    vikram = await db_session.scalar(select(User.id).where(User.phone_e164 == "+919800001008"))
    assert vikram is not None
    assert vikram not in await identity_api.users_with_role(db_session, "hub_manager")  # scoped

    messages = await deliver(db_session, drift())

    assert [m.user_id for m in messages] == [neha]


async def test_an_unknown_hub_is_named_generically_and_a_bad_payload_is_dropped(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    (message,) = await deliver(db_session, drift(hub_id=str(uuid.uuid4())))
    assert message.params["hub"] == "a hub"
    assert await deliver(db_session, {"hub_id": str(SEED_HUB_ID)}) == []  # no live/backtest
