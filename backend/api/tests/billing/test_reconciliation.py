"""Reconciliation with Razorpay (M5 §3.4): lost webhooks healed once, everything else one item."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import razorpay, wallet
from gharbasket.modules.billing.models import Payment, ReconciliationItem, RefundPart
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, OutboxEvent

from ..conftest import make_settings
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .test_refunds import HOOK, KEYS, ask, assert_ledger_balances
from .test_topups import WEBHOOK_SECRET, captured, customer, signed, start

pytestmark = [pytest.mark.anyio, pytest.mark.services]

RECON = "/v1/admin/reconciliation"
ADMIN = "+919800009301"


class Razorpay:
    """Razorpay's API on a mock transport, holding payments and refunds that lists filter by
    `created_at` and page with `skip`/`count`; `down` refuses everything."""

    def __init__(self) -> None:
        self.down = False
        self.payments: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        self.calls = 0
        self.http = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle))
        self.client = razorpay.RazorpayClient(self.http, "rzp_test_fake", "fake-secret")

    def pay(self, order: str, amount: int, ref: str, **more: Any) -> None:
        self.payments[ref] = {
            "id": ref,
            "order_id": order,
            "amount": amount,
            "currency": "INR",
            "status": "captured",
            "created_at": int(datetime.now(UTC).timestamp()),
            **more,
        }

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        if self.down:
            return httpx2.Response(500, json={"error": {"code": "SERVER_ERROR"}})
        path, q = request.url.path.split("/")[2:], request.url.params
        if request.method == "POST" and path == ["orders"]:
            return httpx2.Response(200, json={"id": f"order_r{self.calls}"})
        if request.method == "POST":
            body = json.loads(request.content)
            ref = f"rfnd_r{self.calls}"
            refund = {"id": ref, "payment_id": path[1], "status": "pending", **body}
            refund["created_at"] = int(datetime.now(UTC).timestamp())
            self.refunds[ref] = refund
            return httpx2.Response(200, json=refund)
        if path in (["payments"], ["refunds"]):
            every = self.payments if path == ["payments"] else self.refunds
            items = [e for e in every.values() if int(q["from"]) <= e["created_at"] <= int(q["to"])]
            skip, count = int(q["skip"]), int(q["count"])
            return httpx2.Response(200, json={"items": items[skip : skip + count]})
        if path[-1] == "refunds":
            items = [r for r in self.refunds.values() if r["payment_id"] == path[1]]
            return httpx2.Response(200, json={"items": items})
        if path[1] in self.payments:
            return httpx2.Response(200, json=self.payments[path[1]])
        return httpx2.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR"}})


async def reconcile(db: AsyncSession, gateway: Razorpay | None, at: datetime | None = None):
    run = await run_job(
        db,
        "reconcile_payments",
        settings=make_settings(**KEYS) if gateway else make_settings(),
        scheduled_for=at or datetime.now(UTC),
        http=gateway.http if gateway else None,
    )
    assert run.status == "ok", run.error
    return run.summary or {}


async def paid(api: httpx2.AsyncClient, auth: dict, gateway: Razorpay, amount: int, ref: str):
    """A top-up captured at Razorpay and credited by its webhook."""
    order = (await start(api, auth, amount)).json()["razorpay_order_id"]
    gateway.pay(order, amount, ref)
    event = captured(order, amount, ref)
    assert (await api.post(HOOK, **signed(event, f"evt_{ref}"))).json()["outcome"] == "credited"


async def items(db: AsyncSession) -> list[tuple[str, str, str]]:
    found = await db.execute(select(ReconciliationItem).order_by(ReconciliationItem.id))
    return [(i.kind, i.status, i.razorpay_ref) for i in found.scalars()]


async def admin(api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=ADMIN, sub="uid-recon-admin")
    user = (await db.execute(select(User).where(User.phone_e164 == ADMIN))).scalar_one()
    db.add(UserRole(user_id=user.id, role="admin"))
    await db.commit()
    refreshed = await api.post(
        "/v1/auth/refresh", json={"refresh_token": signed_in["refresh_token"]}
    )
    return bearer(refreshed.json()["access_token"])


async def test_a_lost_top_up_webhook_is_healed_once(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Razorpay()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    topup = (await start(api, auth, 200_000)).json()
    gateway.pay(topup["razorpay_order_id"], 200_000, "pay_lost")  # captured; no webhook came

    first = await reconcile(db_session, gateway)
    again = await reconcile(db_session, gateway)
    late = await api.post(
        HOOK, **signed(captured(topup["razorpay_order_id"], 200_000, "pay_lost"), "e1")
    )

    assert (first["status"], first["healed"], first["opened"]) == ("ok", 1, 0)
    assert again == {"status": "ok", "checked": 2, "healed": 0, "opened": 0}  # a no-op
    assert late.json()["outcome"] == "already_settled"
    assert await wallet.balance(db_session, user) == 200_000  # credited once
    status = (await api.get(f"/v1/wallet/topups/{topup['id']}", headers=auth)).json()["status"]
    assert status == "paid"
    assert await items(db_session) == [("captured_not_credited", "healed", "pay_lost")]
    topped_up = await db_session.execute(
        select(func.count()).where(OutboxEvent.event_type == "billing.wallet_topped_up")
    )
    assert topped_up.scalar_one() == 1
    await assert_ledger_balances(db_session)


@pytest.mark.parametrize("outcome", ["processed", "failed"])
async def test_a_lost_refund_webhook_is_healed_once(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle, outcome: str
) -> None:
    gateway = Razorpay()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    await paid(api, auth, gateway, 300_000, "pay_r")
    refund = (await ask(api, auth, 100_000)).json()
    sent = await run_job(
        db_session, "send_refunds", settings=make_settings(**KEYS), http=gateway.http
    )
    assert (sent.summary or {}).get("sent") == 1
    made = next(iter(gateway.refunds.values()))
    made["status"] = outcome  # Razorpay settled it; the webhook was lost
    made["created_at"] -= 4 * 24 * 3600  # out of the window: found through its payment

    first = await reconcile(db_session, gateway)
    again = await reconcile(db_session, gateway)

    assert (first["healed"], first["opened"], again["healed"], again["opened"]) == (1, 0, 0, 0)
    done = (await api.get(f"/v1/wallet/refunds/{refund['id']}", headers=auth)).json()
    assert (done["status"], done["parts"][0]["status"]) == (outcome, outcome)
    assert await wallet.balance(db_session, user) == (
        200_000 if outcome == "processed" else 300_000
    )
    assert await items(db_session) == [("refund_status_mismatch", "healed", made["id"])]
    await assert_ledger_balances(db_session)


async def test_each_other_mismatch_opens_one_item_however_many_runs_see_it(
    db_session: AsyncSession,
    clients: ClientFactory,
    google: FakeGoogle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(razorpay, "PAGE", 2)  # Razorpay's lists come in pages
    gateway = Razorpay()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    short = (await start(api, auth, 50_000)).json()
    gateway.pay(short["razorpay_order_id"], 40_000, "pay_short")  # less than the top-up
    gateway.pay("order_not_ours", 10_000, "pay_stranger")
    await paid(api, auth, gateway, 100_000, "pay_ghost")
    del gateway.payments["pay_ghost"]  # credited, yet Razorpay has no such payment
    await paid(api, auth, gateway, 100_000, "pay_ok")
    gateway.refunds["rfnd_who"] = {
        "id": "rfnd_who",
        "payment_id": "pay_ok",
        "amount": 5_000,
        "status": "processed",
        "created_at": int(datetime.now(UTC).timestamp()),
    }

    first = await reconcile(db_session, gateway)
    again = await reconcile(db_session, gateway)

    assert (first["opened"], first["healed"], again["opened"]) == (4, 0, 0)
    assert sorted(await items(db_session)) == [
        ("amount_mismatch", "open", "pay_short"),
        ("credited_not_captured", "open", "pay_ghost"),
        ("refund_unknown", "open", "rfnd_who"),
        ("unknown_payment", "open", "pay_stranger"),
    ]
    mismatch = (
        await db_session.execute(
            select(ReconciliationItem).where(ReconciliationItem.kind == "amount_mismatch")
        )
    ).scalar_one()
    await db_session.refresh(mismatch)
    assert (mismatch.expected_paise, mismatch.actual_paise) == (50_000, 40_000)
    assert mismatch.user_id == user and mismatch.last_seen_at > mismatch.first_seen_at
    alerts = await db_session.execute(
        select(OutboxEvent.payload).where(
            OutboxEvent.event_type == "billing.reconciliation_mismatch"
        )
    )
    assert [a["kinds"] for a in alerts.scalars()] == [
        ["amount_mismatch", "credited_not_captured", "refund_unknown", "unknown_payment"]
    ]
    assert await wallet.balance(db_session, user) == 200_000  # nothing moved
    await assert_ledger_balances(db_session)


async def test_a_refund_still_in_flight_after_a_week_is_stuck(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Razorpay()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, _ = await customer(api, google)
    await paid(api, auth, gateway, 100_000, "pay_s")
    await ask(api, auth, 100_000)

    today = await reconcile(db_session, gateway)
    week = await reconcile(db_session, gateway, datetime.now(UTC) + timedelta(days=8))

    assert (today["opened"], week["opened"]) == (0, 1)
    part = (await db_session.execute(select(RefundPart))).scalar_one()
    stuck = (await db_session.execute(select(ReconciliationItem))).scalar_one()
    assert (stuck.kind, stuck.razorpay_ref, stuck.refund_part_id) == (
        "refund_stuck",
        "pay_s",
        part.id,
    )


async def test_razorpay_unavailable_or_no_keys_changes_nothing(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Razorpay()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    topup = (await start(api, auth, 200_000)).json()
    gateway.pay(topup["razorpay_order_id"], 200_000, "pay_lost")
    gateway.down = True

    down = await reconcile(db_session, gateway)
    no_keys = await reconcile(db_session, None)

    assert down == {"status": "failed", "checked": 0, "healed": 0, "opened": 0}
    assert no_keys["status"] == "skipped"
    assert await items(db_session) == []
    assert await wallet.balance(db_session, user) == 0
    payment = await db_session.get(Payment, uuid.UUID(topup["id"]))
    assert payment is not None and payment.status == "created"


async def test_an_admin_lists_and_resolves_an_item_with_a_note_and_it_is_audited(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Razorpay()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    gateway.pay("order_not_ours", 10_000, "pay_stranger")
    topup = (await start(api, auth, 50_000)).json()
    gateway.pay(topup["razorpay_order_id"], 50_000, "pay_lost")
    await reconcile(db_session, gateway)
    boss = await admin(api, google, db_session)

    listed = (await api.get(RECON, headers=boss)).json()
    open_only = (await api.get(RECON, params={"status": "open"}, headers=boss)).json()
    paged = (await api.get(RECON, params={"limit": 1}, headers=boss)).json()
    rest = await api.get(RECON, params={"limit": 1, "cursor": paged["next_cursor"]}, headers=boss)
    item = open_only["items"][0]
    resolved = await api.post(
        f"{RECON}/{item['id']}/resolve", json={"note": "Test payment"}, headers=boss
    )

    assert [i["kind"] for i in listed["items"]] == ["captured_not_credited", "unknown_payment"]
    assert listed["items"][0]["status"] == "healed" and listed["items"][0]["user_id"] == str(user)
    assert listed["last_run"]["status"] == "ok" and listed["last_run"]["healed"] == 1
    assert [i["razorpay_ref"] for i in open_only["items"]] == ["pay_stranger"]
    assert [i["id"] for i in paged["items"] + rest.json()["items"]] == [
        i["id"] for i in listed["items"]
    ]
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved" and body["resolution"]["note"] == "Test payment"
    assert body["actual_paise"] == 10_000 and body["resolution"]["by"] is not None
    audit = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "reconciliation.resolved")
        )
    ).scalar_one()
    assert audit.entity_id == item["id"] and (audit.after or {})["note"] == "Test payment"
    assert await wallet.balance(db_session, user) == 50_000  # resolving moved no money

    again = await api.post(f"{RECON}/{item['id']}/resolve", json={"note": "x"}, headers=boss)
    healed = await api.post(
        f"{RECON}/{listed['items'][0]['id']}/resolve", json={"note": "x"}, headers=boss
    )
    unknown = await api.post(f"{RECON}/{uuid.uuid4()}/resolve", json={"note": "x"}, headers=boss)
    empty = await api.post(f"{RECON}/{item['id']}/resolve", json={"note": ""}, headers=boss)
    assert (again.status_code, again.json()["code"]) == (409, "billing.recon_not_open")
    assert healed.json()["code"] == "billing.recon_not_open"
    assert (unknown.status_code, unknown.json()["code"]) == (404, "billing.recon_not_found")
    assert empty.status_code == 422


async def test_only_admins_see_or_resolve_reconciliation(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, _ = await customer(api, google)

    looked = await api.get(RECON, headers=auth)
    resolved = await api.post(f"{RECON}/{uuid.uuid4()}/resolve", json={"note": "x"}, headers=auth)
    anonymous = await api.get(RECON)

    assert (looked.status_code, looked.json()["code"]) == (403, "auth.forbidden")
    assert resolved.status_code == 403
    assert anonymous.status_code == 401


async def test_a_lost_payment_that_no_longer_fits_is_left_open_never_refunded_by_the_job(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    gateway = Razorpay()
    api = clients(razorpay=gateway.client, razorpay_webhook_secret=WEBHOOK_SECRET)
    auth, user = await customer(api, google)
    late = (await start(api, auth, 100_000)).json()
    await paid(api, auth, gateway, 900_000, "pay_big")
    await wallet.credit_topup(db_session, user, 50_000, payment_ref="pay_cash", topup_id=user)
    await db_session.commit()
    gateway.pay(late["razorpay_order_id"], 100_000, "pay_late")  # would pass the cap now
    calls = gateway.calls

    first = await reconcile(db_session, gateway)
    again = await reconcile(db_session, gateway)

    assert (first["opened"], first["healed"], again["opened"], again["healed"]) == (1, 0, 0, 0)
    assert await items(db_session) == [("captured_not_credited", "open", "pay_late")]
    assert gateway.calls - calls == 4  # two lists a run; no refund sent from here
    payment = await db_session.get(Payment, uuid.UUID(late["id"]), populate_existing=True)
    assert payment is not None and (payment.status, payment.provider_payment_id) == (
        "created",
        None,
    )
    assert await wallet.balance(db_session, user) == 950_000
    await assert_ledger_balances(db_session)
