import hashlib
import hmac
import json
import uuid
from datetime import date
from typing import Any

import httpx2
import pytest
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import wallet
from gharbasket.modules.billing.models import Payment, PaymentWebhook
from gharbasket.modules.billing.razorpay import RazorpayClient

from ..conftest import DEPLOYED_SECRETS
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.conftest import valkey as valkey
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

PHONE = "+919800009201"
WEBHOOK_SECRET = "test-webhook-secret"


class FakeRazorpay:
    """Razorpay's API on a mock transport: orders and refunds, recording what we sent."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[httpx2.Request] = []
        self.client = RazorpayClient(
            httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle)),
            "rzp_test_fake",
            "fake-secret",
        )

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.fail:
            return httpx2.Response(500, json={"error": {"code": "SERVER_ERROR"}})
        n = len(self.requests)
        if request.url.path.endswith("/refund"):
            return httpx2.Response(200, json={"id": f"rfnd_fake{n}", "status": "processed"})
        return httpx2.Response(200, json={"id": f"order_fake{n}", "status": "created"})


async def customer(api: httpx2.AsyncClient, google: FakeGoogle) -> tuple[dict, uuid.UUID]:
    signed = await sign_in(api, google, phone_number=PHONE, sub=f"uid{PHONE}")
    return bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def start(api: httpx2.AsyncClient, auth: dict, amount: int, key: str | None = None):
    return await api.post(
        "/v1/wallet/topups",
        json={"amount_paise": amount},
        headers={**auth, "Idempotency-Key": key or str(uuid.uuid4())},
    )


def signed(body: dict[str, Any], event_id: str, secret: str = WEBHOOK_SECRET) -> dict[str, Any]:
    raw = json.dumps(body).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {
        "content": raw,
        "headers": {
            "content-type": "application/json",
            "x-razorpay-signature": signature,
            "x-razorpay-event-id": event_id,
        },
    }


def captured(order_id: str, amount: int, payment_id: str = "pay_fake1", event="payment.captured"):
    entity = {"id": payment_id, "order_id": order_id, "amount": amount, "currency": "INR"}
    return {"event": event, "payload": {"payment": {"entity": entity}}}


async def test_a_dev_top_up_is_created_paid_and_credited_once(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, _ = await customer(api, google)

    assert (
        await api.post("/v1/wallet/topups", json={"amount_paise": 50_000}, headers=auth)
    ).json()["code"] == "request.idempotency_key_required"
    first = await start(api, auth, 200_000, key="k-200")
    replay = await start(api, auth, 200_000, key="k-200")

    assert first.status_code == 201
    topup = first.json()
    assert replay.json() == topup and replay.headers["idempotent-replayed"] == "true"
    assert topup["status"] == "created" and topup["key_id"] == "rzp_test_dev"
    assert topup["razorpay_order_id"].startswith("order_dev_")
    held = (await api.get("/v1/wallet", headers=auth)).json()
    assert held["balance_paise"] == 0 and held["topup_room_paise"] == 1_000_000 - 200_000

    paid = await api.post(
        "/v1/dev/razorpay/pay", json={"razorpay_order_id": topup["razorpay_order_id"]}, headers=auth
    )
    again = await api.post(
        "/v1/dev/razorpay/pay", json={"razorpay_order_id": topup["razorpay_order_id"]}, headers=auth
    )

    assert paid.status_code == 200 and paid.json()["status"] == "captured"
    assert again.status_code == 200
    status = (await api.get(f"/v1/wallet/topups/{topup['id']}", headers=auth)).json()
    assert status["status"] == "paid"
    body = (await api.get("/v1/wallet", headers=auth)).json()
    assert body["balance_paise"] == 200_000  # credited once
    assert body["entries"][0]["ref"] == {"type": "topup", "id": topup["id"]}
    assert body["topup_room_paise"] == 800_000


async def test_top_ups_respect_the_minimum_the_cap_and_the_monthly_allowance(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, user = await customer(api, google)

    for amount in (9_900, 10_050):
        too_small = await start(api, auth, amount)
        assert (too_small.status_code, too_small.json()["code"]) == (422, "billing.topup_too_small")
    await start(api, auth, 700_000)  # unpaid, but holds its room
    over = await start(api, auth, 400_000)
    assert over.json()["code"] == "billing.wallet_limit"
    assert over.json()["room_paise"] == 300_000

    await db_session.execute(text("UPDATE billing.payments SET status = 'failed'"))  # no holds
    for n in range(2):  # ₹18,000 topped up (and spent) this month already
        await wallet.credit_topup(db_session, user, 900_000, payment_ref=f"m{n}", topup_id=user)
        await wallet.debit_for_order(db_session, user, uuid.uuid4(), 900_000, date(2026, 10, 6))
    monthly = await start(api, auth, 300_000)
    assert monthly.json()["code"] == "billing.monthly_topup_limit"
    assert monthly.json()["room_paise"] == 200_000


async def test_an_unpaid_top_up_expires_and_a_late_payment_that_no_longer_fits_is_refunded(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, _ = await customer(api, google)
    late = (await start(api, auth, 600_000)).json()
    await db_session.execute(
        text("UPDATE billing.payments SET expires_at = now() - interval '1 minute' WHERE id = :i"),
        {"i": late["id"]},
    )

    assert (await api.get(f"/v1/wallet/topups/{late['id']}", headers=auth)).json()[
        "status"
    ] == "expired"
    assert (await api.get("/v1/wallet", headers=auth)).json()["topup_room_paise"] == 1_000_000
    fresh = (await start(api, auth, 600_000)).json()
    for topup in (fresh, late):
        await api.post(
            "/v1/dev/razorpay/pay",
            json={"razorpay_order_id": topup["razorpay_order_id"]},
            headers=auth,
        )

    assert (await api.get(f"/v1/wallet/topups/{late['id']}", headers=auth)).json()[
        "status"
    ] == "refunded"
    assert (await api.get("/v1/wallet", headers=auth)).json()["balance_paise"] == 600_000


async def test_razorpay_orders_and_the_signed_webhook(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    fake = FakeRazorpay()
    api = clients(razorpay=fake.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, _ = await customer(api, google)

    topup = (await start(api, auth, 150_000)).json()
    order = fake.requests[0]
    assert topup["razorpay_order_id"] == "order_fake1" and topup["key_id"] == "rzp_test_fake"
    assert order.url == "https://api.razorpay.com/v1/orders"
    assert order.headers["authorization"].startswith("Basic ")
    assert json.loads(order.content) == {
        "amount": 150_000,
        "currency": "INR",
        "receipt": topup["id"],
        "notes": {"topup_id": topup["id"]},
    }

    hook = "/v1/payments/webhook/razorpay"
    event = captured("order_fake1", 150_000)
    first = await api.post(hook, **signed(event, "evt_1"))
    duplicate = await api.post(hook, **signed(event, "evt_1"))
    redelivered = await api.post(hook, **signed(event, "evt_2"))

    assert [r.json()["outcome"] for r in (first, duplicate, redelivered)] == [
        "credited",
        "duplicate",
        "already_settled",
    ]
    assert (await api.get("/v1/wallet", headers=auth)).json()["balance_paise"] == 150_000
    stored = (await db_session.execute(select(PaymentWebhook.outcome))).scalars().all()
    assert sorted(o for o in stored if o) == ["already_settled", "credited"]

    outcomes = {}
    for name, body in {
        "unknown": captured("order_nope", 100),
        "ignored": {"event": "payment.authorized", "payload": {}},
        "malformed": {"event": "payment.captured", "payload": {"payment": {}}},
    }.items():
        outcomes[name] = (await api.post(hook, **signed(body, f"evt_{name}"))).json()["outcome"]
    assert outcomes == {"unknown": "unknown_order", "ignored": "ignored", "malformed": "malformed"}

    other = (await start(api, auth, 20_000)).json()
    mismatch = await api.post(hook, **signed(captured(other["razorpay_order_id"], 1), "evt_m"))
    failed = await api.post(
        hook,
        **signed(captured(other["razorpay_order_id"], 20_000, "pay_f", "payment.failed"), "evt_f"),
    )
    assert mismatch.json()["outcome"] == "amount_mismatch"
    assert failed.json()["outcome"] == "failed"
    assert (await api.get(f"/v1/wallet/topups/{other['id']}", headers=auth)).json()[
        "status"
    ] == "failed"

    forged = signed(event, "evt_x", secret="not-the-secret")
    assert (await api.post(hook, **forged)).json()["code"] == "billing.bad_signature"
    not_json = {**signed(event, "evt_y"), "content": b"not json"}
    not_json["headers"]["x-razorpay-signature"] = hmac.new(
        WEBHOOK_SECRET.encode(), b"not json", hashlib.sha256
    ).hexdigest()
    assert (await api.post(hook, **not_json)).status_code == 400


async def test_a_late_razorpay_payment_over_the_cap_is_refunded_to_source(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    fake = FakeRazorpay()
    api = clients(razorpay=fake.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    topup = (await start(api, auth, 500_000)).json()
    await wallet.credit_topup(db_session, user, 900_000, payment_ref="pay_other", topup_id=user)

    answer = await api.post(
        "/v1/payments/webhook/razorpay",
        **signed(captured(topup["razorpay_order_id"], 500_000, "pay_late"), "evt_late"),
    )

    assert answer.json()["outcome"] == "refunded_over_cap"
    assert fake.requests[-1].url == "https://api.razorpay.com/v1/payments/pay_late/refund"
    payment = await db_session.get(Payment, uuid.UUID(topup["id"]))
    assert payment is not None
    assert (payment.status, payment.provider_refund_id) == ("refunded", "rfnd_fake2")
    assert await wallet.balance(db_session, user) == 900_000


async def test_razorpay_down_is_a_503_and_nothing_is_kept(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients(razorpay=FakeRazorpay(fail=True).client)
    auth, user = await customer(api, google)

    answer = await start(api, auth, 50_000)

    assert (answer.status_code, answer.json()["code"]) == (503, "billing.payments_unavailable")
    kept = await db_session.execute(select(Payment).where(Payment.user_id == user))
    assert kept.first() is None


async def test_deployed_without_razorpay_there_are_no_dev_payments(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients(app_env="staging", **DEPLOYED_SECRETS)
    auth, _ = await customer(api, google)

    topup = await start(api, auth, 50_000)
    dev = await api.post("/v1/dev/razorpay/pay", json={"razorpay_order_id": "x"}, headers=auth)
    hook = await api.post("/v1/payments/webhook/razorpay", **signed({}, "evt_s"))

    assert topup.json()["code"] == "billing.payments_unavailable"
    assert (dev.status_code, dev.json()["code"]) == (404, "http.not_found")
    assert hook.json()["code"] == "billing.payments_unavailable"


async def test_someone_elses_top_up_is_not_found(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, _ = await customer(api, google)
    mine = (await start(api, auth, 50_000)).json()
    other = await sign_in(api, google, phone_number="+919800009202", sub="uid-other")
    theirs = bearer(other["access_token"])

    looked = await api.get(f"/v1/wallet/topups/{mine['id']}", headers=theirs)
    paid = await api.post(
        "/v1/dev/razorpay/pay",
        json={"razorpay_order_id": mine["razorpay_order_id"]},
        headers=theirs,
    )

    assert looked.json()["code"] == paid.json()["code"] == "billing.topup_not_found"


async def test_more_than_ten_top_ups_an_hour_are_refused(
    clients: ClientFactory, google: FakeGoogle, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    auth, _ = await customer(api, google)

    codes = [(await start(api, auth, 10_000)).status_code for _ in range(11)]

    assert codes == [201] * 10 + [429]
    refused = await start(api, auth, 10_000)
    assert refused.json()["code"] == "billing.too_many_topups"
