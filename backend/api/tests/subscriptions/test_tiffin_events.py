"""A tiffin order (M4 §3.5) on the week's delivery date is another order: its lock, failed
payment or cancellation never moves the weekly cycle."""

import uuid
from typing import Any

import pytest

from gharbasket.modules.subscriptions import jobs, service
from gharbasket.platform.models import OutboxEvent

pytestmark = pytest.mark.anyio


async def test_a_tiffins_events_leave_the_week_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    looked_up: list[Any] = []

    async def cycle_for(*args: Any) -> None:
        looked_up.append(args)

    monkeypatch.setattr(service, "cycle_for", cycle_for)
    payload = {
        "order_id": str(uuid.uuid4()),
        "household_id": str(uuid.uuid4()),
        "delivery_date": "2026-10-13",
        "reason": "payment",
    }
    for handler in (jobs.on_order_locked, jobs.on_order_payment_failed, jobs.on_order_cancelled):
        tiffin = OutboxEvent(event_type="orders.x", payload={**payload, "kind": "tiffin"})
        await handler(None, tiffin)  # pyright: ignore[reportArgumentType]
    assert looked_up == []

    box = OutboxEvent(event_type="orders.locked", payload={**payload, "kind": "box"})
    await jobs.on_order_locked(None, box)  # pyright: ignore[reportArgumentType]
    assert len(looked_up) == 1
