"""Live mode (M5 §3.10, ADR-0037): every payment records the RAZORPAY_MODE it was made in, and
only the running mode's payments are credited, refunded to and reconciled."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
import structlog
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import make_url, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.modules.billing import refunds, wallet
from gharbasket.modules.billing.models import (
    JournalEntry,
    Payment,
    PaymentWebhook,
    ReconciliationItem,
    ReconciliationRun,
    RefundPart,
)
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import run_job

from ..conftest import (
    DEPLOYED_SECRETS,
    LOCAL_TEST_DATABASE_URL,
    OPERATOR_TOKEN,
    alembic_config,
    make_settings,
    run_on_server,
)
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .paid import paid_topup
from .test_reconciliation import Razorpay
from .test_refunds import HOOK, KEYS, Gateway, assert_ledger_balances
from .test_topups import WEBHOOK_SECRET, FakeRazorpay, captured, customer, signed, start

pytestmark = [pytest.mark.anyio, pytest.mark.services]

LIVE: dict[str, Any] = {
    "app_env": "staging",
    **DEPLOYED_SECRETS,
    "razorpay_mode": "live",
    "razorpay_key_id": "rzp_live_fake",
    "razorpay_key_secret": "fake-secret",
    "razorpay_webhook_secret": WEBHOOK_SECRET,
}
BEFORE = "2b9e6c4d8f17"  # the migration before `billing.payments.mode`


async def mode_of(db: AsyncSession, topup_id: str) -> str:
    return (
        await db.execute(select(Payment.mode).where(Payment.id == uuid.UUID(topup_id)))
    ).scalar_one()


async def test_a_new_payment_takes_the_running_mode_and_a_dev_one_is_test(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    dev = clients()  # no keys: a dev order
    auth, _ = await customer(dev, google)
    dev_topup = (await start(dev, auth, 20_000)).json()
    test = clients(razorpay=FakeRazorpay().client, **KEYS)
    test_topup = (await start(test, auth, 20_000)).json()
    live = clients(razorpay=Gateway().client, **LIVE)
    live_auth, _ = await customer(live, google)  # the deployed API signs its own tokens
    live_topup = (await start(live, live_auth, 20_000)).json()

    assert dev_topup["razorpay_order_id"].startswith("order_dev_")
    assert await mode_of(db_session, dev_topup["id"]) == "test"
    assert await mode_of(db_session, test_topup["id"]) == "test"
    assert await mode_of(db_session, live_topup["id"]) == "live"


@pytest.mark.parametrize("event", ["payment.captured", "payment.failed"])
async def test_a_webhook_for_the_other_modes_payment_is_ignored_not_credited(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle, event: str
) -> None:
    test = clients(razorpay=FakeRazorpay().client, **KEYS, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(test, google)
    topup = (await start(test, auth, 200_000)).json()  # a test-mode order, unpaid at the switch
    live = clients(razorpay=Gateway().client, **LIVE)
    body = captured(topup["razorpay_order_id"], 200_000, "pay_other", event=event)

    with structlog.testing.capture_logs() as logs:
        answer = await live.post(HOOK, **signed(body, "evt_other"))

    assert (answer.status_code, answer.json()) == (200, {"outcome": "ignored"})
    payment = await db_session.get(Payment, uuid.UUID(topup["id"]), populate_existing=True)
    assert payment is not None
    assert (payment.status, payment.provider_payment_id) == ("created", None)  # left as it is
    assert await wallet.balance(db_session, user) == 0
    stored = (
        await db_session.execute(
            select(PaymentWebhook).where(PaymentWebhook.event_id == "evt_other")
        )
    ).scalar_one()
    assert (stored.event, stored.outcome) == (event, "ignored")
    mismatch = [line for line in logs if line["event"] == "billing.payment_mode_mismatch"]
    assert mismatch == [
        {
            "event": "billing.payment_mode_mismatch",
            "log_level": "warning",
            "topup_id": topup["id"],
            "mode": "test",
        }
    ]
    # The running mode's own payment on the same API is credited as ever.
    live_auth, _ = await customer(live, google)  # the deployed API signs its own tokens
    mine = (await start(live, live_auth, 100_000)).json()
    credited = await live.post(
        HOOK, **signed(captured(mine["razorpay_order_id"], 100_000, "pay_mine"), "evt_mine")
    )
    assert credited.json() == {"outcome": "credited"}
    assert await wallet.balance(db_session, user) == 100_000


async def test_refund_to_source_takes_only_the_running_modes_top_ups(
    db_session: AsyncSession,
) -> None:
    live = make_settings(**LIVE)
    user = uuid.uuid4()
    old = await paid_topup(db_session, user, 300_000, provider="razorpay", mode="test")
    new = await paid_topup(db_session, user, 100_000, provider="razorpay", mode="live")

    found = await refunds.rooms(db_session, user, live, datetime.now(UTC))
    assert [(r.payment.id, r.room_paise) for r in found] == [(new.id, 100_000)]
    assert await refunds.refundable(db_session, user, live) == 100_000
    # The test top-up's share is what no source can take: refused as more than refundable...
    with pytest.raises(ProblemError) as over:
        await refunds.request_refund(
            db_session, user, 150_000, settings=live, idempotency_key=str(uuid.uuid4())
        )
    assert (over.value.code, over.value.extra) == (
        "billing.refund_exceeds_refundable",
        {"refundable_paise": 100_000},
    )
    # ...and on a closed account it goes to unclaimed, while the live top-up takes its own.
    refund = await refunds.close_wallet(db_session, user, "account_deleted", live)
    assert refund is not None and refund.amount_paise == 100_000
    parts = (
        await db_session.execute(select(RefundPart).where(RefundPart.refund_id == refund.id))
    ).scalars()
    assert [(p.payment_id, p.amount_paise) for p in parts] == [(new.id, 100_000)]
    assert await wallet.balance(db_session, user) == 0
    unclaimed = (
        await db_session.execute(
            select(JournalEntry.kind).where(JournalEntry.idempotency_key == f"unclaimed:{user}")
        )
    ).scalar_one()
    assert unclaimed == "unclaimed"  # the test top-up's ₹3,000
    assert old.id not in {
        p.payment_id for p in (await db_session.execute(select(RefundPart))).scalars()
    }
    # The same wallet in test mode would have refunded the test top-up.
    test = make_settings(**KEYS)
    assert [
        r.payment.id for r in await refunds.rooms(db_session, user, test, datetime.now(UTC))
    ] == [old.id]
    await assert_ledger_balances(db_session)


async def test_send_refunds_never_sends_the_other_modes_part(db_session: AsyncSession) -> None:
    """A test-mode part still pending at the switch is never sent to the live account: it waits,
    and the cut-over's prepare job reports it as a blocker."""
    user = uuid.uuid4()
    top = await paid_topup(db_session, user, 100_000, provider="razorpay", mode="test")
    refund = await refunds.close_wallet(db_session, user, "account_deleted", make_settings(**KEYS))
    assert refund is not None
    await db_session.commit()
    gateway = Gateway()
    run = await run_job(
        db_session,
        "send_refunds",
        settings=make_settings(**LIVE),
        scheduled_for=datetime.now(UTC),
        http=gateway.http,
    )
    assert run.status == "ok", run.error
    assert gateway.requests == []
    part = (
        await db_session.execute(select(RefundPart).where(RefundPart.payment_id == top.id))
    ).scalar_one()
    assert (part.status, part.attempts) == ("pending", 0)
    # In test mode the same part is sent.
    run = await run_job(
        db_session,
        "send_refunds",
        settings=make_settings(**KEYS),
        scheduled_for=datetime.now(UTC),
        http=gateway.http,
    )
    assert run.status == "ok", run.error
    assert len(gateway.requests) >= 1
    await assert_ledger_balances(db_session)


class Recorder(Razorpay):
    """The reconciliation tests' Razorpay, also keeping the path of every request."""

    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path)
        return super().handle(request)


async def test_reconciliation_compares_only_the_running_modes_payments_and_parts(
    db_session: AsyncSession,
) -> None:
    user = uuid.uuid4()
    paid = await paid_topup(db_session, user, 300_000, provider="razorpay", mode="test")
    refund = await refunds.request_refund(
        db_session, user, 100_000, settings=make_settings(**KEYS), idempotency_key="k"
    )
    part = (
        await db_session.execute(select(RefundPart).where(RefundPart.refund_id == refund.id))
    ).scalar_one()
    part.status, part.provider_refund_id = "sent", "rfnd_test1"
    unpaid = Payment(
        user_id=user,
        provider="razorpay",
        mode="test",
        amount_paise=50_000,
        status="created",
        provider_order_id="order_test_unpaid",
        expires_at=datetime.now(UTC),
    )
    db_session.add(unpaid)
    now = datetime.now(UTC)
    earlier = ReconciliationRun(  # a test-mode run opened an item for a payment it then saw
        scheduled_for=now,
        window_from=now,
        window_to=now,
        status="ok",
        checked=1,
        healed=0,
        opened=1,
    )
    db_session.add(earlier)
    await db_session.flush()
    db_session.add(
        ReconciliationItem(
            run_id=earlier.id,
            kind="captured_not_credited",
            status="open",
            razorpay_ref="pay_test_open",
            topup_id=unpaid.id,
            user_id=user,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    await db_session.flush()
    gateway = Recorder()  # the live account: it never saw a test payment, refund or order...
    # ...but for a stray on that order. Stamped with `now`, the run's window end: the fake's own
    # clock could be a second later and fall outside the window (a flake on CI, 22 Nov 2026).
    gateway.pay("order_test_unpaid", 50_000, "pay_live_stray", created_at=int(now.timestamp()))

    async def reconcile(settings: Any, at: datetime) -> dict[str, Any]:
        gateway.paths.clear()
        run = await run_job(
            db_session, "reconcile_payments", settings=settings, scheduled_for=at, http=gateway.http
        )
        assert run.status == "ok", run.error
        return run.summary or {}

    async def open_items() -> list[tuple[str, str]]:
        found = await db_session.execute(
            select(ReconciliationItem.kind, ReconciliationItem.razorpay_ref)
            .where(ReconciliationItem.status == "open")
            .order_by(ReconciliationItem.kind)
        )
        return [(kind, ref) for kind, ref in found.all()]

    lists = ["/v1/payments", "/v1/refunds"]
    live = make_settings(**LIVE)
    # Live: the credited test payment, its sent part and the open item's payment are never
    # looked up, and the stray's order is no live top-up's: an unknown payment, not a heal.
    assert await reconcile(live, now) == {"status": "ok", "checked": 1, "healed": 0, "opened": 1}
    assert gateway.paths == lists
    # Eight days on, the test part is not stuck either: it is not this account's.
    week = await reconcile(live, now + timedelta(days=8))
    assert week == {"status": "ok", "checked": 0, "healed": 0, "opened": 0}
    assert await open_items() == [
        ("captured_not_credited", "pay_test_open"),
        ("unknown_payment", "pay_live_stray"),
    ]
    await db_session.refresh(unpaid)
    assert unpaid.status == "created"
    assert await wallet.balance(db_session, user) == 200_000

    # Test mode: the same rows are ours, and each check sees them.
    gateway.payments.clear()
    test = make_settings(**KEYS)
    assert (await reconcile(test, now))["opened"] == 1
    assert set(gateway.paths) == {
        *lists,
        f"/v1/payments/{paid.provider_payment_id}",
        "/v1/payments/pay_test_open",
        f"/v1/payments/{paid.provider_payment_id}/refunds",
    }
    assert (await reconcile(test, now + timedelta(days=8)))["opened"] == 1
    assert await open_items() == [
        ("captured_not_credited", "pay_test_open"),
        ("credited_not_captured", str(paid.provider_payment_id)),
        ("refund_stuck", "rfnd_test1"),
        ("unknown_payment", "pay_live_stray"),
    ]


async def test_the_ready_check_reports_the_payments_mode_and_health_does_not(
    database_url: str,
) -> None:
    for settings, mode in ((make_settings(), "test"), (make_settings(**LIVE), "live")):
        app = create_app(settings.model_copy(update={"database_url": SecretStr(database_url)}))
        with structlog.testing.capture_logs() as logs, TestClient(app) as client:
            ready = client.get("/v1/ready", headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"})
            health = client.get("/v1/health")
        assert (ready.status_code, ready.json()["payments_mode"]) == (200, mode)
        assert "payments_mode" not in health.json()
        started = [line for line in logs if line["event"] == "api_started"]
        assert [line["payments_mode"] for line in started] == [mode]


def test_the_migration_backfills_every_existing_payment_as_test() -> None:
    """On a database of its own: rows written before the column (dev ones too) become `test`, and
    the check refuses any other mode."""
    base = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)
    name = f"{make_url(base).database}_payment_mode"
    url = make_url(base).set(database=name).render_as_string(hide_password=False)

    async def run(*statements: str) -> list[Any]:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.begin() as connection:
                found: list[Any] = []
                for sql in statements:
                    result = await connection.execute(text(sql))
                    found = list(result.all()) if result.returns_rows else found
                return found
        finally:
            await engine.dispose()

    insert = (
        "INSERT INTO billing.payments (user_id, provider, amount_paise, status, "
        "provider_order_id, expires_at) VALUES "
        "(uuidv7(), 'razorpay', 200000, 'paid', 'order_a', now()), "
        "(uuidv7(), 'dev', 400000, 'paid', 'order_dev_b', now()), "
        "(uuidv7(), 'razorpay', 10000, 'created', 'order_c', now())"
    )
    run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    run_on_server(base, f"CREATE DATABASE {name}")
    try:
        config = alembic_config(url)
        command.upgrade(config, BEFORE)
        asyncio.run(run(insert))
        command.upgrade(config, "head")
        modes = asyncio.run(
            run("SELECT provider_order_id, mode FROM billing.payments ORDER BY provider_order_id")
        )
        assert modes == [("order_a", "test"), ("order_c", "test"), ("order_dev_b", "test")]
        with pytest.raises(Exception, match="ck_payments_mode"):
            asyncio.run(run("UPDATE billing.payments SET mode = 'real'"))
        command.downgrade(config, BEFORE)
        command.upgrade(config, "head")
    finally:
        run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
