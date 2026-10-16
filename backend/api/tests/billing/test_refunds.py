"""Refund to source (M5 §3.3): the request, its ledger entries and closing a wallet."""

import json
import uuid
from datetime import date
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import service, wallet
from gharbasket.modules.billing.models import JournalEntry, Posting
from gharbasket.modules.billing.razorpay import RazorpayClient

from ..conftest import make_settings
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .test_topups import WEBHOOK_SECRET, captured, customer, signed, start

pytestmark = [pytest.mark.anyio, pytest.mark.services]

HOOK = "/v1/payments/webhook/razorpay"
REFUND = "/v1/wallet/refund-to-source"


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

    listed = (await api.get("/v1/wallet/refunds", headers=auth)).json()
    assert [r["id"] for r in listed["items"]] == [out["id"]] and listed["next_cursor"] is None
    shown = (await api.get(f"/v1/wallet/refunds/{out['id']}", headers=auth)).json()
    assert shown == out


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
    await assert_ledger_balances(db_session)
