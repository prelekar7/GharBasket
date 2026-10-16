"""Partner messages (spec §3.1 event map, M8 §10, M9 §3.2): `partners.verified` tells the person
they are verified, in the words of their kind, and `community.captain_assigned` tells the captain
her society is hers. Both are account messages: in the inbox and pushed, whatever the
preferences say, once per (event, person)."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import User
from gharbasket.modules.notifications.jobs import on_event
from gharbasket.modules.notifications.models import Delivery, Message, Preference
from gharbasket.modules.notifications.service import inbox
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish, relay
from gharbasket.tools.seed import seed_partners

from .conftest import MEENA, PRIYA, Home

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NOON = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)  # Mon 12:00 IST
LATE = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)  # Mon 23:30 IST
SAVITA = "+919800001012"


async def messages_for(db: AsyncSession, event_type: str) -> list[Message]:
    rows = await db.execute(
        select(Message).where(Message.event_type == event_type).order_by(Message.id)
    )
    return list(rows.scalars())


async def channels_of(db: AsyncSession, message: Message) -> dict[str, Delivery]:
    rows = await db.execute(select(Delivery).where(Delivery.message_id == message.id))
    return {d.channel: d for d in rows.scalars()}


async def user_id_of(db: AsyncSession, phone: str) -> uuid.UUID:
    found = await db.scalar(select(User.id).where(User.phone_e164 == phone))
    assert found is not None
    return found


@time_machine.travel(NOON, tick=False)
async def test_a_verified_captain_hears_twice_once_per_event_through_the_relay(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    """The real flow: M8 verifies (seed), the relay hands it to M9, M9 appoints her and
    publishes `community.captain_assigned`, the next relay hands that to M1 and M11."""
    await seed_partners(db_session)  # Meena and Savita, each asking for her own society
    for _ in range(3):  # verified → assigned; the last pass finds nothing left
        assert (await relay(db_session)).failed == 0

    meena, savita = homes[MEENA].user_id, await user_id_of(db_session, SAVITA)
    for event_type, kind in (
        ("partners.verified", "partners.verified.captain"),
        ("community.captain_assigned", "community.captain_assigned"),
    ):
        found = await messages_for(db_session, event_type)
        assert sorted(m.user_id for m in found) == sorted([meena, savita]), event_type
        for message in found:
            assert (message.kind, message.category, message.link, message.params) == (
                kind,
                "account",
                "/captain",
                {},
            )
            (push,) = (await channels_of(db_session, message)).values()
            assert (push.channel, push.status) == ("push", "queued")

        # The outbox may deliver an event again: still one message per (event, person).
        for message in found:
            event = await db_session.get(OutboxEvent, message.event_id)
            assert event is not None
            await on_event(db_session, event)
        assert len(await messages_for(db_session, event_type)) == 2

    items, unread, _ = await inbox(db_session, meena, "hi", None, 20)
    assert unread == 2
    assert [i.title for i in items] == ["अब आप अपनी सोसाइटी की कैप्टन हैं", "आप सत्यापित सोसाइटी कैप्टन हैं"]


@pytest.mark.parametrize(
    ("partner_kind", "title", "link"),
    [
        ("kitchen", "Your kitchen is verified on GharBasket", "/kitchen"),
        ("rider", "You're a verified GharBasket rider", "/rider"),
        ("packer", "You're a verified GharBasket packer", "/packer"),
    ],
)
async def test_each_partner_kind_gets_its_own_words_and_home(
    db_session: AsyncSession, homes: dict[str, Home], partner_kind: str, title: str, link: str
) -> None:
    priya = homes[PRIYA].user_id
    # Late at night, with push off for every category she can choose: account messages are not
    # a preference, but they still wait for the end of quiet hours (they are not critical).
    db_session.add_all(
        Preference(user_id=priya, category=c, push=False, whatsapp=False)
        for c in ("weekly_box", "delivery", "money", "kindness")
    )
    await db_session.flush()
    payload = {
        "partner_id": str(uuid.uuid4()),
        "user_id": str(priya),
        "kind": partner_kind,
        "hub_id": str(uuid.uuid4()),
        "requested_society_id": None,
    }
    with time_machine.travel(LATE, tick=False):
        event_id = await publish(db_session, "partners.verified", payload)
        assert (await relay(db_session)).failed == 0

    (message,) = await messages_for(db_session, "partners.verified")
    assert (message.event_id, message.user_id, message.kind, message.category, message.link) == (
        event_id,
        priya,
        f"partners.verified.{partner_kind}",
        "account",
        link,
    )
    (push,) = (await channels_of(db_session, message)).values()
    assert (push.channel, push.status) == ("push", "held")
    items, _, _ = await inbox(db_session, priya, "en", None, 20)
    assert [i.title for i in items] == [title]


@pytest.mark.parametrize(
    ("event_type", "payload", "error"),
    [
        ("partners.verified", {"kind": "chef"}, "ValueError"),  # not a partner kind
        ("partners.verified", {"kind": "rider", "user_id": "someone"}, "ValueError"),
        ("partners.verified", {"user_id": "{user}"}, "KeyError"),  # no kind
        ("partners.verified", {"kind": ["rider"], "user_id": "{user}"}, "ValueError"),
        ("community.captain_assigned", {"society_id": str(uuid.uuid4())}, "KeyError"),
        ("community.captain_assigned", {"user_id": 42}, "ValueError"),
    ],
)
async def test_a_bad_partner_payload_is_logged_and_skipped_without_raising(
    db_session: AsyncSession,
    homes: dict[str, Home],
    event_type: str,
    payload: dict[str, Any],
    error: str,
) -> None:
    user = str(homes[PRIYA].user_id)
    payload = {k: user if v == "{user}" else v for k, v in payload.items()}
    event = OutboxEvent(id=uuid.uuid4(), event_type=event_type, payload=payload)
    with structlog.testing.capture_logs() as logs:
        await on_event(db_session, event)  # must not raise: other consumers share the savepoint
    assert await messages_for(db_session, event_type) == []
    (dropped,) = [e for e in logs if e["event"] == "notification_event_unreadable"]
    assert (dropped["event_type"], dropped["error"]) == (event_type, error)
