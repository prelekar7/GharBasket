"""Refund to source (M5 §3.3): the request and its ledger, the send_refunds job, the webhooks."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import service, wallet
from gharbasket.modules.billing.models import JournalEntry, Posting, RefundPart
from gharbasket.modules.billing.razorpay import RazorpayClient
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent

from ..conftest import make_settings
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .test_topups import WEBHOOK_SECRET, captured, customer, signed, start

pytestmark = [pytest.mark.anyio, pytest.mark.services]

HOOK = "/v1/payments/webhook/razorpay"
REFUND = "/v1/wallet/refund-to-source"
KEYS = {"razorpay_key_id": "rzp_test_fake", "razorpay_key_secret": "fake-secret"}
T0 = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)


class Gateway:
    """Razorpay on a mock transport, keeping the refunds it made: `lose` drops the answer after
    the refund is made (a timeout), `down` refuses everything."""

    def __init__(self) -> None:
        self.lose = self.down = False
        self.requests: list[httpx2.Request] = []
        self.made: dict[str, list[dict[str, Any]]] = {}
        self.http = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle))
        self.client = RazorpayClient(self.http, "rzp_test_fake", "fake-secret")

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.down:
            return httpx2.Response(500, json={"error": {"code": "SERVER_ERROR"}})
        path, n = request.url.path, len(self.requests)
        if path == "/v1/orders":
            return httpx2.Response(200, json={"id": f"order_g{n}", "status": "created"})
        payment = path.split("/")[3]
        if request.method == "GET":
            items = self.made.get(payment, [])
            return httpx2.Response(200, json={"entity": "collection", "items": items})
        body = json.loads(request.content)
        refund = {"id": f"rfnd_g{n}", "payment_id": payment, "status": "pending", **body}
        self.made.setdefault(payment, []).append(refund)
        if self.lose:
            raise httpx2.ReadTimeout("lost", request=request)
        return httpx2.Response(200, json=refund)

    def posts(self) -> int:
        return sum(r.method == "POST" and r.url.path.endswith("/refund") for r in self.requests)


async def dev_topup(api: httpx2.AsyncClient, auth: dict, amount: int) -> str:
    topup = (await start(api, auth, amount)).json()
    paid = await api.post(
        "/v1/dev/razorpay/pay", json={"razorpay_order_id": topup["razorpay_order_id"]}, headers=auth
    )
    assert paid.status_code == 200, paid.text
    return topup["id"]


async def razorpay_topup(api: httpx2.AsyncClient, auth: dict, amount: int, payment: str) -> str:
    topup = (await start(api, auth, amount)).json()
    event = captured(topup["razorpay_order_id"], amount, payment)
    assert (await api.post(HOOK, **signed(event, f"evt_{payment}"))).json()["outcome"] == "credited"
    return topup["id"]


async def ask(api: httpx2.AsyncClient, auth: dict, amount: int | None, key: str | None = None):
    return await api.post(
        REFUND,
        json={"amount_paise": amount},
        headers={**auth, "Idempotency-Key": key or str(uuid.uuid4())},
    )


def refund_event(kind: str, refund_id: str, receipt: str | None, payment: str = "pay_x") -> dict:
    entity = {"id": refund_id, "payment_id": payment, "amount": 1, "receipt": receipt}
    return {"event": kind, "payload": {"refund": {"entity": entity}}}


async def send(db: AsyncSession, at: datetime = T0, gateway: Gateway | None = None) -> dict:
    settings = make_settings(**KEYS) if gateway else make_settings()
    run = await run_job(
        db,
        "send_refunds",
        settings=settings,
        scheduled_for=at,
        http=gateway.http if gateway else None,
    )
    assert run.status == "ok", run.error
    return run.summary or {}


async def assert_ledger_balances(db: AsyncSession) -> None:
    unbalanced = await db.execute(
        select(Posting.entry_id).group_by(Posting.entry_id).having(func.sum(Posting.amount) != 0)
    )
    assert unbalanced.all() == []


async def kinds(db: AsyncSession, user: uuid.UUID) -> list[str]:
    account = await service.find_account(db, "wallet", "user", user)
    assert account is not None
    rows = await db.execute(
        select(JournalEntry.kind)
        .join(Posting, Posting.entry_id == JournalEntry.id)
        .where(Posting.account_id == account.id)
        .order_by(JournalEntry.id)
    )
    return list(rows.scalars())


async def settled_events(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload).where(
            OutboxEvent.event_type == "billing.refund_to_source_settled"
        )
    )
    return list(rows.scalars())


async def test_a_refund_is_split_newest_first_posted_at_once_and_holds_its_room(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, user = await customer(api, google)
    older = await dev_topup(api, auth, 200_000)
    newer = await dev_topup(api, auth, 300_000)
    await wallet.debit_for_order(db_session, user, uuid.uuid4(), 100_000, date(2026, 10, 10))
    await db_session.commit()
    assert (await api.get("/v1/wallet", headers=auth)).json()["refundable_paise"] == 400_000

    no_key = await api.post(REFUND, json={"amount_paise": 1_000}, headers=auth)
    too_small = await ask(api, auth, 99)
    too_much = await ask(api, auth, 400_100)
    first = await ask(api, auth, 350_000, key="r-1")
    replay = await ask(api, auth, 350_000, key="r-1")
    again = await ask(api, auth, 1_000)

    assert no_key.json()["code"] == "request.idempotency_key_required"
    assert (too_small.status_code, too_small.json()["code"]) == (422, "billing.refund_too_small")
    assert too_much.json()["code"] == "billing.refund_exceeds_refundable"
    assert too_much.json()["refundable_paise"] == 400_000
    assert first.status_code == 202, first.text
    out = first.json()
    assert replay.json() == out and replay.headers["idempotent-replayed"] == "true"
    assert (out["status"], out["reason"], out["amount_paise"]) == ("pending", "customer", 350_000)
    assert [(p["topup_id"], p["amount_paise"], p["status"]) for p in out["parts"]] == [
        (newer, 300_000, "pending"),
        (older, 50_000, "pending"),
    ]
    assert out["parts"][0]["razorpay_payment_id"].startswith("pay_dev_")
    assert (again.status_code, again.json()["code"]) == (409, "billing.refund_in_progress")
    assert again.json()["refund_id"] == out["id"]

    body = (await api.get("/v1/wallet", headers=auth)).json()
    assert body["balance_paise"] == 50_000  # posted at once
    assert body["topup_room_paise"] == 1_000_000 - 50_000 - 350_000  # in flight holds its room
    assert body["refundable_paise"] == 50_000
    assert body["entries"][0]["kind"] == "refund_to_source"
    assert body["entries"][0]["ref"] == {"type": "refund", "id": out["id"]}
    assert (
        await kinds(db_session, user) == ["topup", "topup", "box_debit"] + ["refund_to_source"] * 2
    )
    await assert_ledger_balances(db_session)

    summary = await send(db_session)  # dev parts settle at once

    assert summary["dev_settled"] == 2 and summary["stuck"] == 0
    done = (await api.get(f"/v1/wallet/refunds/{out['id']}", headers=auth)).json()
    assert done["status"] == "processed" and done["settled_at"] is not None
    assert {p["status"] for p in done["parts"]} == {"processed"}
    assert done["parts"][0]["razorpay_refund_id"].startswith("rfnd_dev_")
    listed = (await api.get("/v1/wallet/refunds", headers=auth)).json()
    assert [r["id"] for r in listed["items"]] == [out["id"]] and listed["next_cursor"] is None
    assert (await api.get("/v1/wallet", headers=auth)).json()["topup_room_paise"] == 950_000
    assert await settled_events(db_session) == [
        {
            "refund_id": out["id"],
            "user_id": str(user),
            "amount_paise": 350_000,
            "processed_paise": 350_000,
            "status": "processed",
        }
    ]
    assert (await send(db_session))["dev_settled"] == 0  # nothing sent twice


async def test_everything_refundable_and_at_most_three_a_day(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, _ = await customer(api, google)
    for n in range(3):
        await dev_topup(api, auth, 10_000)
        everything = await ask(api, auth, None)
        assert everything.json()["amount_paise"] == 10_000, everything.text
        await send(db_session)
        if n == 0:  # an empty wallet has nothing to send back (refusals don't count)
            nothing = await ask(api, auth, None)
            assert nothing.json()["code"] == "billing.refund_too_small"
            assert nothing.json()["refundable_paise"] == 0

    await dev_topup(api, auth, 10_000)
    fourth = await ask(api, auth, None)
    assert (fourth.status_code, fourth.json()["code"]) == (429, "billing.too_many_refunds")


async def test_only_top_ups_within_the_refund_window_take_money_back(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, user = await customer(api, google)
    old = await dev_topup(api, auth, 200_000)
    await dev_topup(api, auth, 50_000)
    await db_session.execute(
        text("UPDATE billing.payments SET created_at = now() - interval '181 days' WHERE id = :i"),
        {"i": old},
    )
    await db_session.commit()

    assert (await api.get("/v1/wallet", headers=auth)).json()["refundable_paise"] == 50_000
    assert (await ask(api, auth, 60_000)).json()["refundable_paise"] == 50_000
    assert await wallet.balance(db_session, user) == 250_000


async def test_the_job_never_refunds_twice_after_a_lost_answer(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Gateway()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, _ = await customer(api, google)
    await razorpay_topup(api, auth, 200_000, "pay_one")
    refund = (await ask(api, auth, 150_000)).json()
    assert refund["parts"][0]["razorpay_payment_id"] == "pay_one"
    gateway.lose = True

    lost = await send(db_session, T0, gateway)
    gateway.lose = False
    early = await send(db_session, T0 + timedelta(minutes=1), gateway)
    retried = await send(db_session, T0 + timedelta(minutes=6), gateway)

    assert (lost["retrying"], early["found"], retried["found"]) == (1, 0, 1)
    assert gateway.posts() == 1  # the retry listed the refunds and matched our receipt
    made = gateway.made["pay_one"][0]
    assert (made["amount"], made["speed"]) == (150_000, "normal")
    sent = (await api.get(f"/v1/wallet/refunds/{refund['id']}", headers=auth)).json()
    assert sent["status"] == "processing"
    assert (
        sent["parts"][0]["status"] == "sent"
        and sent["parts"][0]["razorpay_refund_id"] == made["id"]
    )

    event = refund_event("refund.processed", made["id"], made["receipt"], "pay_one")
    first = await api.post(HOOK, **signed(event, "evt_r1"))
    duplicate = await api.post(HOOK, **signed(event, "evt_r1"))
    redelivered = await api.post(HOOK, **signed(event, "evt_r2"))

    assert [r.json()["outcome"] for r in (first, duplicate, redelivered)] == [
        "refund_processed",
        "duplicate",
        "already_settled",
    ]
    done = (await api.get(f"/v1/wallet/refunds/{refund['id']}", headers=auth)).json()
    assert done["status"] == "processed" and done["parts"][0]["status"] == "processed"
    assert (await send(db_session, T0 + timedelta(hours=1), gateway))["sent"] == 0
    assert gateway.posts() == 1
    await assert_ledger_balances(db_session)


async def test_a_failed_refund_goes_back_into_the_wallet_and_always_fits(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Gateway()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    await razorpay_topup(api, auth, 1_000_000, "pay_full")
    refund = (await ask(api, auth, 400_000)).json()
    assert (await send(db_session, T0, gateway))["sent"] == 1

    full = await start(api, auth, 10_000)
    assert full.json()["code"] == "billing.wallet_limit" and full.json()["room_paise"] == 0
    with pytest.raises(ProblemError) as late:  # a top-up paid now would not fit either
        await wallet.credit_topup(db_session, user, 10_000, payment_ref="p2", topup_id=user)
    assert late.value.code == "billing.wallet_limit"

    made = gateway.made["pay_full"][0]
    failed = await api.post(
        HOOK, **signed(refund_event("refund.failed", made["id"], made["receipt"]), "evt_f")
    )

    assert failed.json()["outcome"] == "refund_failed"
    assert await wallet.balance(db_session, user) == 1_000_000  # back in, within the cap
    done = (await api.get(f"/v1/wallet/refunds/{refund['id']}", headers=auth)).json()
    assert (done["status"], done["parts"][0]["status"]) == ("failed", "failed")
    assert await kinds(db_session, user) == ["topup", "refund_to_source", "refund_to_source_failed"]
    assert (await settled_events(db_session))[0]["processed_paise"] == 0
    period = datetime.now(wallet.IST).strftime("%Y-%m")
    statement = (await api.get(f"/v1/statements?period={period}", headers=auth)).json()
    assert statement["entries"][0]["kind"] == "refund_to_source_failed"
    assert statement["entries"][0]["amount_paise"] == 400_000
    await assert_ledger_balances(db_session)


async def test_a_refund_razorpay_never_answers_is_stuck_but_never_failed(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Gateway()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    await razorpay_topup(api, auth, 100_000, "pay_s")
    await ask(api, auth, 100_000)
    gateway.down = True
    without_keys = await send(db_session, T0)  # no keys: the part waits untried

    runs = [await send(db_session, T0 + timedelta(minutes=m), gateway) for m in (0, 5, 35, 120)]

    assert without_keys["unavailable"] == 1
    assert [r["retrying"] for r in runs] == [1, 1, 1, 0]
    assert runs[-1]["stuck"] == 1
    part = (await db_session.execute(select(RefundPart))).scalar_one()
    assert (part.status, part.attempts, part.last_error) == ("pending", 3, "razorpay_unavailable")
    assert await wallet.in_flight_paise(db_session, user) == 100_000


async def test_refund_events_that_are_not_ours(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Gateway()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    await db_session.execute(
        text(
            "INSERT INTO billing.payments (user_id, provider, amount_paise, status, "
            "provider_order_id, provider_payment_id, provider_refund_id, expires_at) VALUES "
            "(uuidv7(), 'razorpay', 100, 'refunded', 'order_o', 'pay_o', 'rfnd_over', now())"
        )
    )
    await db_session.commit()

    outcomes = {}
    for name, body in {
        "unknown": refund_event("refund.processed", "rfnd_nope", str(uuid.uuid4())),
        "over_cap": refund_event("refund.processed", "rfnd_over", None),
        "created": refund_event("refund.created", "rfnd_nope", None),
        "malformed": {"event": "refund.failed", "payload": {}},
    }.items():
        outcomes[name] = (await api.post(HOOK, **signed(body, f"evt_{name}"))).json()["outcome"]

    assert outcomes == {
        "unknown": "unknown_refund",
        "over_cap": "already_settled",
        "created": "ignored",
        "malformed": "malformed",
    }


async def test_only_the_owner_sees_a_refund(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    auth, _ = await customer(api, google)
    await dev_topup(api, auth, 10_000)
    mine = (await ask(api, auth, None)).json()
    other = await sign_in(api, google, phone_number="+919800009203", sub="uid-other-r")
    theirs = bearer(other["access_token"])

    looked = await api.get(f"/v1/wallet/refunds/{mine['id']}", headers=theirs)
    listed = await api.get("/v1/wallet/refunds", headers=theirs)
    anonymous = await api.post(REFUND, json={}, headers={"Idempotency-Key": "anon"})

    assert (looked.status_code, looked.json()["code"]) == (404, "billing.refund_not_found")
    assert listed.json()["items"] == []
    assert anonymous.status_code == 401


async def test_closing_a_deleted_account_refunds_what_it_can_and_the_rest_is_unclaimed(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Gateway()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    await razorpay_topup(api, auth, 300_000, "pay_a")
    await razorpay_topup(api, auth, 200_000, "pay_b")
    await wallet.credit_topup(db_session, user, 5_050, payment_ref="pay_gone", topup_id=user)

    closed = await billing_api.close_wallet(db_session, user, settings=make_settings())
    await db_session.commit()

    assert closed is not None and closed.reason == "account_deleted"
    assert [(p.razorpay_payment_id, p.amount_paise) for p in closed.parts] == [
        ("pay_b", 200_000),
        ("pay_a", 300_000),
    ]
    assert await wallet.balance(db_session, user) == 0
    unclaimed = await service.platform_account(db_session, "unclaimed_balances")
    assert await service.balance(db_session, unclaimed) == 5_050  # no top-up could take it
    assert not await billing_api.wallet_closed(db_session, user)
    assert (
        await billing_api.close_wallet(db_session, user, settings=make_settings()) is None
    )  # nothing left

    await send(db_session, T0, gateway)
    b, a = gateway.made["pay_b"][0], gateway.made["pay_a"][0]
    await api.post(HOOK, **signed(refund_event("refund.failed", b["id"], b["receipt"]), "e1"))
    await api.post(HOOK, **signed(refund_event("refund.processed", a["id"], a["receipt"]), "e2"))

    done = (await api.get(f"/v1/wallet/refunds/{closed.id}", headers=auth)).json()
    assert done["status"] == "partly_failed"
    assert await service.balance(db_session, unclaimed) == 205_050  # no top-up had room for it
    assert await billing_api.wallet_closed(db_session, user)
    assert (await settled_events(db_session))[0]["processed_paise"] == 300_000
    await assert_ledger_balances(db_session)


async def test_a_deleted_accounts_failed_part_is_moved_once_then_unclaimed(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Gateway()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    await razorpay_topup(api, auth, 300_000, "pay_a")
    await razorpay_topup(api, auth, 200_000, "pay_b")
    await wallet.debit_for_order(db_session, user, uuid.uuid4(), 300_000, date(2026, 10, 10))
    closed = await billing_api.close_wallet(db_session, user, settings=make_settings())
    await db_session.commit()
    assert closed is not None and [p.amount_paise for p in closed.parts] == [200_000]

    await send(db_session, T0, gateway)
    b = gateway.made["pay_b"][0]
    await api.post(HOOK, **signed(refund_event("refund.failed", b["id"], b["receipt"]), "e1"))
    moved = (await api.get(f"/v1/wallet/refunds/{closed.id}", headers=auth)).json()
    assert [(p["razorpay_payment_id"], p["status"]) for p in moved["parts"]] == [
        ("pay_b", "failed"),
        ("pay_a", "pending"),
    ]
    assert moved["status"] == "processing" and await wallet.balance(db_session, user) == 0

    await send(db_session, T0, gateway)
    a = gateway.made["pay_a"][0]
    await api.post(HOOK, **signed(refund_event("refund.failed", a["id"], a["receipt"]), "e2"))

    done = (await api.get(f"/v1/wallet/refunds/{closed.id}", headers=auth)).json()
    assert done["status"] == "failed"
    unclaimed = await service.platform_account(db_session, "unclaimed_balances")
    assert await service.balance(db_session, unclaimed) == 200_000
    assert await billing_api.wallet_closed(db_session, user)
    assert await kinds(db_session, user) == [
        "topup",
        "topup",
        "box_debit",
        "refund_to_source",
        "refund_to_source_failed",
        "refund_to_source",
        "refund_to_source_failed",
        "unclaimed",
    ]
    await assert_ledger_balances(db_session)
