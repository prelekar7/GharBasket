"""Customer statements (spec §3.2): a wallet's month from the ledger, in IST months."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import service, statements, wallet
from gharbasket.modules.billing.models import JournalEntry, Posting
from gharbasket.modules.billing.wallet import IST
from gharbasket.platform.errors import ProblemError

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def customer(clients: ClientFactory, google: FakeGoogle, phone: str) -> tuple:
    api = clients()
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return api, bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def posted_at(session: AsyncSession, user: uuid.UUID, amount: int, at: datetime) -> None:
    """A balanced wallet entry stamped at `at` (the service stamps the real clock): + into it."""
    mine = await service.owned_account(session, "wallet", "user", user, kind="liability")
    clearing = await service.platform_account(session, "razorpay_clearing")
    entry = JournalEntry(
        kind="topup" if amount > 0 else "adjustment",
        idempotency_key=f"test:{uuid.uuid4()}",
        created_at=at,
    )
    session.add(entry)
    await session.flush()
    session.add_all(
        [
            Posting(entry_id=entry.id, account_id=clearing.id, amount=amount),
            Posting(entry_id=entry.id, account_id=mine.id, amount=-amount),
        ]
    )
    await session.flush()


def this_month() -> datetime:
    return datetime.now(IST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def test_an_empty_month_is_zeros(clients: ClientFactory, google: FakeGoogle) -> None:
    api, auth, _ = await customer(clients, google, "+919800009201")
    period = this_month().strftime("%Y-%m")

    response = await api.get(f"/v1/statements?period={period}", headers=auth)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert datetime.fromisoformat(body.pop("generated_at")) <= datetime.now(UTC)
    assert body == {
        "period": period,
        "currency": "INR",
        "opening_paise": 0,
        "credits_paise": 0,
        "debits_paise": 0,
        "closing_paise": 0,
        "entries": [],
    }


async def test_a_month_reconciles_with_the_wallet_and_carries_a_running_balance(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google, "+919800009202")
    start = this_month()
    await posted_at(db_session, user, 50_000, start - timedelta(days=1))  # last month
    topup, order = uuid.uuid4(), uuid.uuid4()
    await wallet.credit_topup(db_session, user, 200_000, payment_ref="pay_s1", topup_id=topup)
    await billing_api.debit_for_order(db_session, user, order, 69_000, date(2026, 10, 6))
    await billing_api.refund_to_wallet(db_session, user, order, 2_500, "shortage")

    body = (await api.get(f"/v1/statements?period={start:%Y-%m}", headers=auth)).json()
    previous = (start - timedelta(days=1)).strftime("%Y-%m")
    before = (await api.get(f"/v1/statements?period={previous}", headers=auth)).json()
    balance = (await api.get("/v1/wallet", headers=auth)).json()["balance_paise"]

    assert (body["opening_paise"], body["credits_paise"], body["debits_paise"]) == (
        50_000,
        202_500,
        -69_000,
    )
    assert (
        body["closing_paise"] == 183_500 == balance == await billing_api.balance(db_session, user)
    )
    assert before["closing_paise"] == body["opening_paise"]
    assert [(e["kind"], e["amount_paise"], e["balance_after_paise"]) for e in body["entries"]] == [
        ("refund", 2_500, 183_500),
        ("box_debit", -69_000, 181_000),
        ("topup", 200_000, 250_000),
    ]
    assert body["entries"][1]["ref"] == {
        "type": "order",
        "id": str(order),
        "delivery_date": "2026-10-06",
    }
    assert body["entries"][2]["ref"] == {"type": "topup", "id": str(topup)}


async def test_the_running_balance_follows_posting_order(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google, "+919800009203")
    for amount in (10_000, -3_000, 5_000, -12_000, 700):
        await posted_at(db_session, user, amount, datetime(2026, 7, 15, 6, 0, tzinfo=UTC))
    await posted_at(db_session, user, 4_000, datetime(2026, 6, 30, 12, 0, tzinfo=UTC))

    body = (await api.get("/v1/statements?period=2026-07", headers=auth)).json()

    oldest_first = body["entries"][::-1]
    assert [e["amount_paise"] for e in oldest_first] == [10_000, -3_000, 5_000, -12_000, 700]
    running = body["opening_paise"]
    for e in oldest_first:
        running += e["amount_paise"]
        assert e["balance_after_paise"] == running
    assert body["opening_paise"] == 4_000
    assert body["closing_paise"] == running == 4_000 + 15_700 - 15_000


async def test_an_entry_falls_in_its_ist_month(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google, "+919800009204")
    last_minute = datetime(2026, 8, 31, 23, 59, tzinfo=IST)  # 18:29 UTC on the 31st
    first_minute = datetime(2026, 9, 1, 0, 1, tzinfo=IST)  # 18:31 UTC on 31 August
    await posted_at(db_session, user, 100, last_minute)
    await posted_at(db_session, user, 200, first_minute)

    august = (await api.get("/v1/statements?period=2026-08", headers=auth)).json()
    september = (await api.get("/v1/statements?period=2026-09", headers=auth)).json()

    assert [e["amount_paise"] for e in august["entries"]] == [100]
    assert (august["opening_paise"], august["closing_paise"]) == (0, 100)
    assert [e["amount_paise"] for e in september["entries"]] == [200]
    assert (september["opening_paise"], september["closing_paise"]) == (100, 300)
    assert datetime.fromisoformat(september["entries"][0]["at"]) == first_minute


async def test_a_customer_sees_only_their_own_statement(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, _ = await customer(clients, google, "+919800009205")
    _, other_auth, other = await customer(clients, google, "+919800009206")
    await wallet.credit_topup(
        db_session, other, 30_000, payment_ref="pay_s2", topup_id=uuid.uuid4()
    )
    period = this_month().strftime("%Y-%m")

    mine = (await api.get(f"/v1/statements?period={period}", headers=auth)).json()
    theirs = (await api.get(f"/v1/statements?period={period}", headers=other_auth)).json()

    assert (mine["closing_paise"], mine["entries"]) == (0, [])
    assert theirs["closing_paise"] == 30_000
    assert (await api.get(f"/v1/statements?period={period}")).status_code == 401


async def test_a_period_is_a_month_up_to_the_current_one(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, _ = await customer(clients, google, "+919800009207")
    next_month = (this_month() + timedelta(days=32)).strftime("%Y-%m")

    for period in (
        "2026-13",
        "2026-00",
        "2026-1",
        "26-10",
        "2026-10-01",
        "oct-2026",
        "",
        "0999-01",
    ):
        response = await api.get(f"/v1/statements?period={period}", headers=auth)
        assert response.status_code == 422, period
        assert response.json()["code"] == "billing.period_invalid", period
    future = await api.get(f"/v1/statements?period={next_month}", headers=auth)
    assert (future.status_code, future.json()["code"]) == (422, "billing.period_invalid")
    missing = await api.get("/v1/statements", headers=auth)
    assert (missing.status_code, missing.json()["code"]) == (422, "request.invalid")


def test_the_current_month_is_taken_in_ist() -> None:
    before_midnight = datetime(2026, 10, 31, 18, 29, tzinfo=UTC)  # 23:59 IST on 31 October
    after_midnight = datetime(2026, 10, 31, 18, 31, tzinfo=UTC)  # 00:01 IST on 1 November

    with pytest.raises(ProblemError):
        statements.parse_period("2026-11", now=before_midnight)
    november = statements.parse_period("2026-11", now=after_midnight)
    december = statements.parse_period("2025-12", now=after_midnight)

    assert november.start == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    assert november.end == datetime(2026, 11, 30, 18, 30, tzinfo=UTC)
    assert december.end == datetime(2025, 12, 31, 18, 30, tzinfo=UTC)
