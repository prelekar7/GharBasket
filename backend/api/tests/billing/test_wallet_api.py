import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import wallet
from gharbasket.platform.db import create_engine

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

PHONE = "+919800009101"


async def customer(clients: ClientFactory, google: FakeGoogle) -> tuple:
    api = clients()
    signed = await sign_in(api, google, phone_number=PHONE, sub=f"uid{PHONE}")
    return api, bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def test_a_new_customer_has_an_empty_wallet_with_full_room(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, _ = await customer(clients, google)

    response = await api.get("/v1/wallet", headers=auth)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "balance_paise": 0,
        "cap_paise": 1_000_000,
        "topup_room_paise": 1_000_000,
        "refundable_paise": 0,
        "entries": [],
    }
    assert (await api.get("/v1/wallet")).status_code == 401
    assert (await api.get("/v1/wallet/entries", headers=auth)).json() == {
        "items": [],
        "next_cursor": None,
    }


async def test_entries_come_newest_first_with_their_refs(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google)
    topup, order = uuid.uuid4(), uuid.uuid4()
    await wallet.credit_topup(db_session, user, 200_000, payment_ref="pay_w1", topup_id=topup)
    await billing_api.debit_for_order(db_session, user, order, 69_000, date(2026, 10, 6))
    await billing_api.refund_to_wallet(db_session, user, order, 2_500, "shortage")

    body = (await api.get("/v1/wallet", headers=auth)).json()

    assert body["balance_paise"] == 133_500
    assert body["topup_room_paise"] == 1_000_000 - 133_500
    kinds = [(e["kind"], e["amount_paise"], e["ref"]) for e in body["entries"]]
    assert kinds == [
        ("refund", 2_500, {"type": "order", "id": str(order), "delivery_date": "2026-10-06"}),
        ("box_debit", -69_000, {"type": "order", "id": str(order), "delivery_date": "2026-10-06"}),
        ("topup", 200_000, {"type": "topup", "id": str(topup)}),
    ]
    assert body["entries"][0]["at"].endswith("Z") or "+00:00" in body["entries"][0]["at"]
    assert await billing_api.balance(db_session, user) == 133_500


async def test_entries_page_by_cursor(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google)
    for n in range(5):
        await wallet.credit_topup(
            db_session, user, 1_000, payment_ref=f"pay_p{n}", topup_id=uuid.uuid4()
        )

    first = (await api.get("/v1/wallet/entries?limit=2", headers=auth)).json()
    second = (
        await api.get(f"/v1/wallet/entries?limit=2&cursor={first['next_cursor']}", headers=auth)
    ).json()
    last = (
        await api.get(f"/v1/wallet/entries?limit=2&cursor={second['next_cursor']}", headers=auth)
    ).json()

    ids = [e["id"] for page in (first, second, last) for e in page["items"]]
    assert len(ids) == len(set(ids)) == 5
    assert last["next_cursor"] is None
    bad = await api.get("/v1/wallet/entries?cursor=bm90LWEtY3Vyc29y", headers=auth)
    assert bad.status_code == 422
    assert bad.json()["code"] == "request.invalid_cursor"


async def test_this_months_topups_limit_the_room(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google)
    for n in range(2):
        await wallet.credit_topup(
            db_session, user, 900_000, payment_ref=f"pay_m{n}", topup_id=uuid.uuid4()
        )
        await billing_api.debit_for_order(
            db_session, user, uuid.uuid4(), 900_000, date(2026, 10, 6)
        )

    body = (await api.get("/v1/wallet", headers=auth)).json()

    assert body["balance_paise"] == 0
    assert body["topup_room_paise"] == 2_000_000 - 1_800_000


async def test_two_debits_at_the_same_moment_cannot_overdraw(database_url: str) -> None:
    """Separate connections, so this commits: it leaves one random user's rows in the test DB."""
    engine = create_engine(database_url, pooled=False)
    user = uuid.uuid4()
    try:
        async with AsyncSession(engine) as session:
            await wallet.credit_topup(
                session, user, 50_000, payment_ref=f"pay_race_{user}", topup_id=uuid.uuid4()
            )
            await session.commit()

        async def debit() -> str:
            async with AsyncSession(engine) as session:
                try:
                    await billing_api.debit_for_order(
                        session, user, uuid.uuid4(), 40_000, date(2026, 10, 6)
                    )
                    await asyncio.sleep(0.05)  # hold the lock while the other one waits
                    await session.commit()
                    return "paid"
                except billing_api.InsufficientBalance:
                    return "short"

        results = await asyncio.gather(debit(), debit())

        assert sorted(results) == ["paid", "short"]
        async with AsyncSession(engine) as session:
            assert await billing_api.balance(session, user) == 10_000
    finally:
        await engine.dispose()
