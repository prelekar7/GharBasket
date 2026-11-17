"""The cut-over to live money (M5 §3.10, ADR-0037): `billing_cutover_prepare` pauses synthetic
households and reports what still holds test money; `billing_cutover_live` closes it in one
`test_mode_closed` entry; the probe `billing.live_mode_mixed` watches the books afterwards."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import cutover, kindness, refunds, statements, wallet
from gharbasket.modules.billing.models import (
    JournalEntry,
    LedgerAccount,
    Payment,
    Posting,
    ReconciliationItem,
    ReconciliationRun,
)
from gharbasket.modules.billing.router import _entry  # pyright: ignore[reportPrivateUsage]
from gharbasket.modules.community.models import Society
from gharbasket.modules.identity.models import Household, User
from gharbasket.modules.orders.models import CarryOver, Order, OrderEvent, OrderIssue, Slot
from gharbasket.modules.subscriptions.models import Plan, Subscription, WeeklyCycle
from gharbasket.modules.subscriptions.service import is_paused_on
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, JobRun, OutboxEvent

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from .paid import paid_topup
from .test_live_mode import LIVE
from .test_refunds import KEYS

pytestmark = [pytest.mark.anyio, pytest.mark.services]

HUB = uuid.uuid4()


async def run(db: AsyncSession, name: str, **settings: Any) -> JobRun:
    return await run_job(
        db, name, settings=make_settings(**(settings or LIVE)), scheduled_for=datetime.now(UTC)
    )


async def household(db: AsyncSession, society: Society, source: str) -> Subscription:
    user = User(phone_e164=f"+9198{uuid.uuid4().int % 10**8:08d}", data_source=source)
    db.add(user)
    await db.flush()
    home = Household(
        owner_user_id=user.id,
        society_id=society.id,
        hub_id=HUB,
        size=3,
        diet="veg",
        weekday_cook_minutes=30,
        weekend_cook_minutes=60,
        data_source=source,
    )
    db.add(home)
    await db.flush()
    plan = (await db.execute(select(Plan.id).where(Plan.code == "autopilot"))).scalar_one()
    subscription = Subscription(household_id=home.id, plan_id=plan, payment_mode="wallet")
    db.add(subscription)
    await db.flush()
    return subscription


async def week(
    db: AsyncSession, subscription: Subscription, cutoff_in: timedelta, status: str = "approved"
) -> WeeklyCycle:
    cutoff = datetime.now(UTC) + cutoff_in
    cycle = WeeklyCycle(
        household_id=subscription.household_id,
        hub_id=HUB,
        society_id=uuid.uuid4(),
        subscription_id=subscription.id,
        week_start=cutoff.date(),
        delivery_date=cutoff.date() + timedelta(days=1),
        slot_id=uuid.uuid4(),
        cutoff_at=cutoff,
        plan_source="ml",
        status=status,
    )
    db.add(cycle)
    await db.flush()
    return cycle


async def test_both_jobs_refuse_unless_the_api_runs_live(
    db_session: AsyncSession, clients: ClientFactory
) -> None:
    for name in ("billing_cutover_prepare", "billing_cutover_live"):
        done = await run(db_session, name, **KEYS)
        assert done.status == "failed"
        assert (done.summary or {})["problem"]["code"] == "billing.cutover_not_live"
        answer = await clients().post(f"/v1/internal/jobs/{name}", json={})
        assert answer.status_code == 409, answer.text
        body = answer.json()
        assert body["code"] == "billing.cutover_not_live" and "run_id" in body
    assert (
        await db_session.scalar(select(JournalEntry).where(JournalEntry.kind == cutover.KEY))
        is None
    )


async def test_prepare_pauses_only_synthetic_households_and_skips_their_open_weeks(
    db_session: AsyncSession,
) -> None:
    society = Society(slug=f"s-{uuid.uuid4().hex[:8]}", name="Sunrise")
    db_session.add(society)
    await db_session.flush()
    real = await household(db_session, society, "real")
    synthetic = await household(db_session, society, "synthetic")
    real_week = await week(db_session, real, timedelta(days=1))
    open_week = await week(db_session, synthetic, timedelta(days=1))
    locked_week = await week(db_session, synthetic, timedelta(days=-1), status="locked")
    synthetic.paused_until = date(2027, 1, 1)  # a dated pause becomes one with no end date
    await db_session.flush()

    first = await run(db_session, "billing_cutover_prepare")

    assert first.status == "ok", first.error
    summary = first.summary or {}
    assert summary["paused"] >= 1 and summary["cycles_skipped"] >= 1
    assert set(summary["blockers"]) == {
        "open_paid_orders",
        "open_carry_overs",
        "refund_parts_in_flight",
        "unpaid_test_topups",
        "open_recon_items",
        "live_topups_open",
    }
    for row in (real, synthetic, real_week, open_week, locked_week):
        await db_session.refresh(row)
    assert (synthetic.status, synthetic.paused_until) == ("paused", None)
    assert (real.status, real.paused_until) == ("active", None)
    assert (open_week.status, locked_week.status, real_week.status) == (
        "skipped",
        "locked",
        "approved",
    )
    again = await run(db_session, "billing_cutover_prepare")
    assert again.status == "ok", again.error
    assert ((again.summary or {})["paused"], (again.summary or {})["cycles_skipped"]) == (0, 0)


async def test_prepare_reports_each_blocker(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    before = await cutover.blockers(db_session, now)
    slot = Slot(hub_id=HUB, label="Morning", start_time=time(7), end_time=time(9), capacity=50)
    db_session.add(slot)
    await db_session.flush()

    def order(status: str) -> Order:
        return Order(
            household_id=uuid.uuid4(),
            hub_id=HUB,
            society_id=uuid.uuid4(),
            delivery_date=now.date(),
            slot_id=slot.id,
            cutoff_at=now - timedelta(days=1),
            subtotal_paise=60_000,
            kind="box",
            status=status,
            fees_paise=0,
            total_paise=60_000,
        )

    # Counted: one in progress, one delivered an hour ago, one refunded with an open issue.
    # Not: approved, payment_failed, delivered three days ago.
    rows = {s: order(s) for s in ("packed", "approved", "payment_failed", "refunded")}
    recent, old = order("delivered"), order("delivered")
    db_session.add_all([*rows.values(), recent, old])
    await db_session.flush()
    db_session.add_all(
        [
            OrderEvent(
                order_id=recent.id,
                to_status="delivered",
                actor="rider",
                at=now - timedelta(hours=1),
            ),
            OrderEvent(
                order_id=old.id, to_status="delivered", actor="rider", at=now - timedelta(days=3)
            ),
            OrderIssue(
                order_id=rows["refunded"].id,
                household_id=rows["refunded"].household_id,
                hub_id=HUB,
                kind="missing",
                photo_keys=[],
                amount_paise=5_000,
                created_by=uuid.uuid4(),
                status="open",
                open_reason="triage",
            ),
            CarryOver(
                from_order_id=old.id,
                household_id=old.household_id,
                hub_id=HUB,
                society_id=old.society_id,
                product_id=uuid.uuid4(),
                steps=1,
                step_qty=500,
                step_unit="g",
                paid_paise=4_000,
                missed_on=now.date(),
                expires_on=now.date() + timedelta(days=14),
            ),
        ]
    )
    # A test-mode refund part left pending; a test top-up not paid yet; an open item; a live
    # top-up credited before the close.
    leaving = uuid.uuid4()
    await paid_topup(db_session, leaving, 50_000, provider="razorpay", mode="test")
    assert await refunds.close_wallet(db_session, leaving, "account_deleted", make_settings(**KEYS))
    db_session.add(
        Payment(
            user_id=uuid.uuid4(),
            provider="razorpay",
            mode="test",
            amount_paise=20_000,
            status="created",
            provider_order_id=f"order_{uuid.uuid4().hex}",
            expires_at=now + timedelta(minutes=20),
        )
    )
    recon = ReconciliationRun(
        scheduled_for=now,
        window_from=now,
        window_to=now,
        status="ok",
        checked=0,
        healed=0,
        opened=1,
    )
    db_session.add(recon)
    await db_session.flush()
    db_session.add(
        ReconciliationItem(
            run_id=recon.id,
            kind="unknown_payment",
            status="open",
            razorpay_ref=f"pay_{uuid.uuid4().hex}",
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    await paid_topup(db_session, uuid.uuid4(), 10_000, provider="razorpay", mode="live")
    await db_session.flush()

    done = await run(db_session, "billing_cutover_prepare")

    assert done.status == "ok", done.error
    after = (done.summary or {})["blockers"]
    assert {k: after[k] - before[k] for k in after} == {
        "open_paid_orders": 3,
        "open_carry_overs": 1,
        "refund_parts_in_flight": 1,
        "unpaid_test_topups": 1,
        "open_recon_items": 1,
        "live_topups_open": 1,
    }

    # Live refuses with the same list, and posts nothing.
    blocked = await run(db_session, "billing_cutover_live")
    assert blocked.status == "failed"
    problem = (blocked.summary or {})["problem"]
    assert (problem["status"], problem["code"]) == (409, "billing.cutover_blocked")
    assert problem["extra"]["blockers"] == after
    assert (
        await db_session.scalar(select(JournalEntry).where(JournalEntry.kind == cutover.KEY))
        is None
    )


async def balances(db: AsyncSession, currency: str = "INR") -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(Posting.account_id, func.sum(Posting.amount))
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(LedgerAccount.currency == currency)
        .group_by(Posting.account_id)
    )
    return {account: int(total) for account, total in rows.all() if total}


async def test_the_close_zeroes_every_inr_account_once_and_leaves_points_alone(
    db_session: AsyncSession, clients: ClientFactory
) -> None:
    priya, cook = uuid.uuid4(), uuid.uuid4()
    await paid_topup(db_session, priya, 50_000)
    await wallet.debit_for_order(db_session, priya, uuid.uuid4(), 20_000, date(2026, 11, 3))
    await paid_topup(db_session, cook, 10_000)
    await kindness.credit_points(db_session, priya, 120, "donation", uuid.uuid4())
    assert await balances(db_session)
    points = await balances(db_session, "KINDNESS_PTS")

    first = await run(db_session, "billing_cutover_live")

    assert first.status == "ok", first.error
    summary = first.summary or {}
    assert summary["already_done"] is False and summary["accounts"] >= 4
    assert await balances(db_session) == {}
    assert await balances(db_session, "KINDNESS_PTS") == points
    assert (await kindness.balance(db_session, priya)).points == 120
    entry = await db_session.get(JournalEntry, uuid.UUID(summary["entry_id"]))
    assert entry is not None and (entry.kind, entry.ref_type) == ("test_mode_closed", "cutover")
    legs = (await db_session.execute(select(Posting).where(Posting.entry_id == entry.id))).scalars()
    legs = list(legs)
    assert sum(p.amount for p in legs) == 0
    assert summary["total_paise"] == sum(p.amount for p in legs if p.amount > 0)
    listed = {
        (a["code"], a["owner_id"]): a["amount_paise"] for a in (entry.details or {})["accounts"]
    }
    assert listed[("wallet", str(priya))] == 30_000  # the 300 rupees left, debited to zero
    assert listed[("wallet", str(cook))] == 10_000
    audit = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.entity_id == str(entry.id)))
    ).scalar_one()
    assert audit.action == "billing.cutover_live"
    assert (audit.after or {})["total_paise"] == summary["total_paise"]
    event = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "billing.cutover_completed")
        )
    ).scalar_one()
    assert event.payload == {
        "entry_id": str(entry.id),
        "accounts": summary["accounts"],
        "total_paise": summary["total_paise"],
    }

    # Her statement shows one line, "Test balance closed", and ends at zero.
    month = statements.parse_period(datetime.now(UTC).astimezone(wallet.IST).strftime("%Y-%m"))
    shown = await statements.statement(db_session, priya, month)
    line = shown.lines[0]
    assert (_entry(line.row).kind, line.row.amount_paise, shown.closing_paise) == (
        "test_mode_closed",
        -30_000,
        0,
    )

    # Again: the same entry, nothing more; over HTTP too.
    again = await run(db_session, "billing_cutover_live")
    assert again.status == "ok", again.error
    assert again.summary == {**summary, "already_done": True}
    answer = await clients(**LIVE).post(
        "/v1/internal/jobs/billing_cutover_live",
        json={},
        headers={"Authorization": "Bearer op-token"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["summary"]["entry_id"] == summary["entry_id"]
    count = select(func.count()).where(JournalEntry.kind == cutover.KEY)
    assert await db_session.scalar(count) == 1


async def test_the_probe_reports_live_money_left_unclosed_for_a_day(
    db_session: AsyncSession,
) -> None:
    await paid_topup(db_session, uuid.uuid4(), 20_000)  # test money
    now = datetime.now(UTC)
    day = timedelta(hours=24)

    # Since the API started live: a day without the close is a problem; an hour is not.
    assert await cutover.mixed(db_session, now, mode="live", started_at=now - day) != []
    assert (
        await cutover.mixed(db_session, now, mode="live", started_at=now - timedelta(hours=1)) == []
    )
    assert await cutover.mixed(db_session, now, mode="test", started_at=now - day) == []
    # Since the first live payment, which a restart does not reset.
    live = await paid_topup(db_session, uuid.uuid4(), 10_000, provider="razorpay", mode="live")
    live.created_at = now + timedelta(minutes=1)  # after the test money
    await db_session.flush()
    later = now + timedelta(hours=25)
    assert await cutover.mixed(db_session, later, mode="live", started_at=later) == [
        "the API runs live and the test money is not closed after 24 h; run "
        "billing_cutover_prepare, then billing_cutover_live (docs/runbooks/live-cutover.md)"
    ]


async def test_the_probe_reports_a_test_top_up_credited_after_the_close(
    db_session: AsyncSession,
) -> None:
    await paid_topup(db_session, uuid.uuid4(), 20_000)
    now = datetime.now(UTC)
    assert (await cutover.close(db_session, now))["entry_id"]
    assert (
        await cutover.mixed(db_session, now, mode="live", started_at=now - timedelta(days=2)) == []
    )

    await paid_topup(db_session, uuid.uuid4(), 10_000, provider="razorpay", mode="live")
    assert await cutover.mixed(db_session, now, mode="live", started_at=now) == []
    await paid_topup(db_session, uuid.uuid4(), 10_000, mode="test")
    found = ["1 test-mode top-ups credited after the test money was closed"]
    assert await cutover.mixed(db_session, now, mode="live", started_at=now) == found
    assert await cutover.mixed(db_session, now, mode="test", started_at=now) == found


def test_a_pause_with_no_end_date_holds_every_week_until_resumed() -> None:
    day = date(2026, 12, 1)
    assert is_paused_on(Subscription(status="paused", paused_until=None), day)
    assert not is_paused_on(Subscription(status="active", paused_until=None), day)
    assert is_paused_on(Subscription(status="paused", paused_until=date(2026, 12, 2)), day)
    assert not is_paused_on(Subscription(status="paused", paused_until=day), day)
