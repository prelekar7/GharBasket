"""Events become messages (spec §3.1 event map and rules): the right kind, values and person,
once per event, a delivery per channel the person wants, and quiet hours."""

import uuid
from datetime import UTC, datetime, time
from typing import Any

import pytest
import structlog
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Consent
from gharbasket.modules.notifications.jobs import on_event
from gharbasket.modules.notifications.models import Delivery, Message, Preference, QuietHours
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import CONSUMERS, publish, relay

from .conftest import MEENA, PRIYA, Home

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NOON = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)  # Mon 12:00 IST
LATE = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)  # Mon 23:30 IST
SEVEN_AM = datetime(2026, 10, 13, 1, 30, tzinfo=UTC)  # Tue 07:00 IST


async def deliver(db: AsyncSession, event_type: str, payload: dict[str, Any]) -> uuid.UUID:
    """Hand one event to the consumer, as the relay would; returns its id."""
    event = OutboxEvent(id=uuid.uuid4(), event_type=event_type, payload=payload)
    await on_event(db, event)
    return event.id


async def messages_of(db: AsyncSession, event_id: uuid.UUID) -> list[Message]:
    return list((await db.execute(select(Message).where(Message.event_id == event_id))).scalars())


async def deliveries_of(db: AsyncSession, message_id: uuid.UUID) -> dict[str, Delivery]:
    rows = await db.execute(select(Delivery).where(Delivery.message_id == message_id))
    return {d.channel: d for d in rows.scalars()}


@time_machine.travel(NOON, tick=False)
async def test_each_mapped_event_makes_its_message_for_the_right_person(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA]
    household = {"household_id": str(priya.household_id), "order_id": str(uuid.uuid4())}
    order = {**household, "delivery_date": "2026-10-13", "total_paise": 69_000}
    cutoff = {"cutoff_at": "2026-10-12T15:30:00Z", "total_paise": 69_000}
    cases: list[tuple[str, dict[str, Any], str, dict[str, Any], str | None]] = [
        (
            "subscriptions.box_ready",
            {**household, **cutoff, "delivery_date": "2026-10-13"},
            "subscriptions.box_ready",
            {
                "total": 69_000,
                "cutoff_day": cutoff["cutoff_at"],
                "cutoff_time": cutoff["cutoff_at"],
            },
            "/box",
        ),
        (
            "subscriptions.cutoff_reminder",
            {**household, **cutoff},
            "subscriptions.cutoff_reminder",
            {"cutoff_time": cutoff["cutoff_at"]},
            "/week",
        ),
        (
            "subscriptions.box_approved",
            {**order, "auto_approved": True, "lines": []},
            "subscriptions.box_approved",
            {"day": "2026-10-13"},
            "/box",
        ),
        (
            "orders.locked",
            order,
            "orders.locked",
            {"total": 69_000, "day": "2026-10-13"},
            "/orders",
        ),
        (
            "orders.payment_failed",
            {**order, "shortfall_paise": 9_000, "grace_until": "2026-10-12T17:30:00+00:00"},
            "orders.payment_failed",
            {"shortfall": 9_000, "grace_time": "2026-10-12T17:30:00+00:00"},
            "/wallet",
        ),
        (
            "orders.cancelled",
            {**order, "reason": "payment"},
            "orders.cancelled",
            {"day": "2026-10-13"},
            "/week",
        ),
        ("orders.dispatched", order, "orders.dispatched", {}, "/orders"),
        ("orders.at_society", order, "orders.at_society", {}, "/orders"),
        ("orders.delivered", order, "orders.delivered", {}, "/orders"),
        ("orders.delivery_failed", order, "orders.delivery_failed", {}, "/orders"),
        (
            "orders.shortage_substituted",
            {**household, "product_id": str(uuid.uuid4()), "refund_paise": 4_500},
            "orders.shortage_substituted",
            {"refund": 4_500},
            "/orders",
        ),
        (
            "orders.issue_resolved",
            {**household, "status": "credited", "credit_paise": 12_000, "by": "auto"},
            "orders.issue_resolved.credited",
            {"credit": 12_000},
            "/orders",
        ),
        (
            "orders.issue_resolved",
            {**household, "status": "rejected", "credit_paise": 0, "by": "hub_manager"},
            "orders.issue_resolved.rejected",
            {},
            "/orders",
        ),
        (
            "billing.wallet_topped_up",
            {"user_id": str(priya.user_id), "topup_id": str(uuid.uuid4()), "amount_paise": 200_000},
            "billing.wallet_topped_up",
            {"amount": 200_000},
            "/wallet",
        ),
        (
            "identity.data_export_ready",
            {"user_id": str(priya.user_id), "export_id": str(uuid.uuid4())},
            "identity.data_export_ready",
            {},
            "/profile",
        ),
    ]
    for event_type, payload, kind, params, link in cases:
        event_id = await deliver(db_session, event_type, payload)
        (message,) = await messages_of(db_session, event_id)
        assert (message.user_id, message.event_type, message.kind) == (
            priya.user_id,
            event_type,
            kind,
        ), kind
        assert (message.params, message.link) == (params, link), kind
        # Default preferences: pushed now, nothing on WhatsApp.
        (push,) = (await deliveries_of(db_session, message.id)).values()
        assert (push.channel, push.status, push.next_attempt_at) == ("push", "queued", NOON)

    meena = homes[MEENA]
    other = {**order, "household_id": str(meena.household_id)}
    (to_meena,) = await messages_of(db_session, await deliver(db_session, "orders.locked", other))
    assert to_meena.user_id == meena.user_id

    silent = [
        ("subscriptions.box_approved", {**order, "auto_approved": False}),
        ("subscriptions.box_approved", order),  # before M3 adds the field
        ("orders.cancelled", {**order, "reason": "customer"}),
        ("orders.cancelled", {**order, "reason": "skipped"}),
        ("orders.shortage_substituted", {"order_id": str(uuid.uuid4()), "refund_paise": 100}),
        ("orders.locked", {**order, "household_id": str(uuid.uuid4())}),  # no such household
        ("orders.issue_reported", {**household, "kind": "missing"}),  # not mapped
        ("subscriptions.cycle_generated", order),  # still a draft
    ]
    for event_type, payload in silent:
        assert await messages_of(db_session, await deliver(db_session, event_type, payload)) == []


@time_machine.travel(NOON, tick=False)
async def test_an_event_delivered_twice_makes_one_message_and_one_delivery_per_channel(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA]
    db_session.add(Preference(user_id=priya.user_id, category="delivery", push=True, whatsapp=True))
    db_session.add(Consent(user_id=priya.user_id, purpose="whatsapp", version="v1"))
    await db_session.flush()
    payload = {"household_id": str(priya.household_id), "order_id": str(uuid.uuid4())}
    event_id = await publish(db_session, "orders.dispatched", payload)
    assert (await relay(db_session)).failed == 0

    event = await db_session.get(OutboxEvent, event_id)
    assert event is not None
    await on_event(db_session, event)  # the outbox may deliver it again
    await on_event(db_session, event)

    (message,) = await messages_of(db_session, event_id)
    deliveries = await deliveries_of(db_session, message.id)
    assert sorted(deliveries) == ["push", "whatsapp"]
    assert {d.status for d in deliveries.values()} == {"queued"}


@time_machine.travel(NOON, tick=False)
async def test_an_unreadable_payload_is_dropped_and_the_other_consumers_still_run(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA]
    seen: list[str] = []

    async def spy(session: AsyncSession, event: OutboxEvent) -> None:
        seen.append(event.event_type)

    CONSUMERS["billing.wallet_topped_up"].append(spy)
    try:
        with structlog.testing.capture_logs() as logs:
            await publish(
                db_session,
                "billing.wallet_topped_up",
                {"user_id": str(priya.user_id), "amount_paise": "a lot"},
            )
            await publish(db_session, "orders.locked", {"household_id": "not-a-uuid"})
            await publish(db_session, "orders.delivered", {"order_id": str(uuid.uuid4())})
            relayed = await relay(db_session)
    finally:
        CONSUMERS["billing.wallet_topped_up"].remove(spy)

    assert relayed.failed == 0
    assert seen == ["billing.wallet_topped_up"]
    assert (await db_session.execute(select(Message))).scalars().all() == []
    dropped = [e for e in logs if e["event"] == "notification_event_unreadable"]
    assert sorted((e["event_type"], e["error"]) for e in dropped) == [
        ("billing.wallet_topped_up", "ValueError"),
        ("orders.delivered", "KeyError"),
        ("orders.locked", "ValueError"),
    ]
    not_an_object = OutboxEvent(id=uuid.uuid4(), event_type="orders.locked", payload=[])
    await on_event(db_session, not_an_object)  # logged and dropped too
    assert await messages_of(db_session, not_an_object.id) == []


async def test_quiet_hours_hold_a_push_until_they_end_and_critical_events_skip_them(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya, meena = homes[PRIYA], homes[MEENA]
    order = {"household_id": str(priya.household_id), "order_id": str(uuid.uuid4())}
    # Meena keeps a shorter night: 23:45 to 06:00.
    db_session.add(QuietHours(user_id=meena.user_id, start_time=time(23, 45), end_time=time(6)))
    await db_session.flush()

    with time_machine.travel(LATE, tick=False):
        dispatched = await deliver(db_session, "orders.dispatched", order)
        at_society = await deliver(db_session, "orders.at_society", order)
        failed = await deliver(
            db_session,
            "orders.payment_failed",
            {**order, "shortfall_paise": 100, "grace_until": "2026-10-12T17:30:00Z"},
        )
        hers = await deliver(
            db_session, "orders.dispatched", {**order, "household_id": str(meena.household_id)}
        )

    async def push(event_id: uuid.UUID) -> Delivery:
        (message,) = await messages_of(db_session, event_id)
        return (await deliveries_of(db_session, message.id))["push"]

    held = await push(dispatched)
    assert (held.status, held.next_attempt_at) == ("held", SEVEN_AM)
    for critical in (at_society, failed):
        sent_now = await push(critical)
        assert (sent_now.status, sent_now.next_attempt_at) == ("queued", LATE)
    hers_now = await push(hers)
    assert (hers_now.status, hers_now.next_attempt_at) == ("queued", LATE)  # before 23:45


@time_machine.travel(NOON, tick=False)
async def test_preferences_decide_push_and_whatsapp_but_never_the_inbox(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA]
    db_session.add_all(
        [
            Preference(user_id=priya.user_id, category="delivery", push=False, whatsapp=False),
            Preference(user_id=priya.user_id, category="money", push=False, whatsapp=True),
        ]
    )
    await db_session.flush()
    order = {"household_id": str(priya.household_id), "order_id": str(uuid.uuid4())}
    locked = {**order, "total_paise": 100, "delivery_date": "2026-10-13"}

    async def channels(event_type: str, payload: dict[str, Any]) -> list[str]:
        (message,) = await messages_of(db_session, await deliver(db_session, event_type, payload))
        return sorted(await deliveries_of(db_session, message.id))

    assert await channels("orders.delivered", order) == []  # in the inbox only
    assert await channels("orders.locked", locked) == []  # WhatsApp on, but no consent
    db_session.add(Consent(user_id=priya.user_id, purpose="whatsapp", version="v1"))
    await db_session.flush()
    assert await channels("orders.locked", locked) == ["whatsapp"]
    await db_session.execute(
        update(Consent).where(Consent.user_id == priya.user_id).values(revoked_at=NOON)
    )
    assert await channels("orders.locked", locked) == []  # revoked: checked for every message
    # Account messages are always pushed; they are not a preference.
    export = {"user_id": str(priya.user_id), "export_id": str(uuid.uuid4())}
    assert await channels("identity.data_export_ready", export) == ["push"]
