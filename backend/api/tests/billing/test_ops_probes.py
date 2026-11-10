"""M5's ops probes (M0 §6.1, spec §3.4): what the `ops_check` watchdog reports about payments.
`billing.refunds_stuck` is checked with the stuck refund itself, in test_refunds.py."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import jobs
from gharbasket.modules.billing.models import PaymentWebhook, ReconciliationItem, ReconciliationRun

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


async def test_a_webhook_left_unprocessed_for_15_minutes_is_reported(
    db_session: AsyncSession,
) -> None:
    def hook(event_id: str, minutes_ago: int, processed: bool) -> PaymentWebhook:
        at = NOW - timedelta(minutes=minutes_ago)
        return PaymentWebhook(
            provider="razorpay",
            event_id=event_id,
            event="payment.captured",
            payload={},
            received_at=at,
            processed_at=at if processed else None,
        )

    db_session.add_all(
        [hook("evt_old", 20, False), hook("evt_new", 5, False), hook("evt_done", 60, True)]
    )
    await db_session.flush()

    assert await jobs.webhook_backlog(db_session, NOW) == ["1 webhooks unprocessed for > 15 min"]
    assert await jobs.webhook_backlog(db_session, NOW - timedelta(hours=1)) == []


async def test_items_open_for_a_day_are_counted_by_kind(db_session: AsyncSession) -> None:
    run = ReconciliationRun(
        scheduled_for=NOW,
        window_from=NOW,
        window_to=NOW,
        status="ok",
        checked=0,
        healed=0,
        opened=3,
    )
    db_session.add(run)
    await db_session.flush()

    def item(kind: str, ref: str, hours_ago: int, status: str = "open") -> ReconciliationItem:
        seen = NOW - timedelta(hours=hours_ago)
        return ReconciliationItem(
            run_id=run.id,
            kind=kind,
            status=status,
            razorpay_ref=ref,
            first_seen_at=seen,
            last_seen_at=seen,
        )

    db_session.add_all(
        [
            item("unknown_payment", "pay_a", 30),
            item("unknown_payment", "pay_b", 25),
            item("refund_stuck", "pay_c", 48),
            item("unknown_payment", "pay_d", 2),  # not a day yet
            item("refund_stuck", "pay_e", 72, status="resolved"),
        ]
    )
    await db_session.flush()

    assert await jobs.reconciliation_open(db_session, NOW) == [
        "3 reconciliation items open for > 24 h (refund_stuck x1, unknown_payment x2); "
        "see /reconciliation"
    ]
