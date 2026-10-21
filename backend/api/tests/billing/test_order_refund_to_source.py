"""An order refund the wallet cannot take goes to the payment source (M5 §3.3, "Order refunds when
the wallet is full"): one `refund` entry as ever, plus a Refund whose parts send it back."""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import refunds, service, settlement, wallet
from gharbasket.modules.billing.models import Refund, RefundPart
from gharbasket.platform.errors import ProblemError

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .paid import paid_topup
from .test_refunds import (
    ask,
    assert_ledger_balances,
    dev_topup,
    kinds,
    refund_event,
    send,
    settled_events,
)
from .test_topups import customer

pytestmark = [pytest.mark.anyio, pytest.mark.services]

CAP = wallet.CAP_PAISE


async def refund_rows(db: AsyncSession, user: uuid.UUID) -> list[Refund]:
    found = await db.execute(select(Refund).where(Refund.user_id == user).order_by(Refund.id))
    return list(found.scalars())


async def parts_of(db: AsyncSession, refund: Refund) -> list[RefundPart]:
    found = await db.execute(
        select(RefundPart).where(RefundPart.refund_id == refund.id).order_by(RefundPart.id)
    )
    return list(found.scalars())


async def full_wallet(db: AsyncSession, user: uuid.UUID, *sizes: int) -> list[uuid.UUID]:
    """Paid top-ups of these sizes, the first the oldest; returns their ids."""
    ids = []
    for n, size in enumerate(sizes):
        ids.append((await paid_topup(db, user, size, age_days=10 - 2 * n)).id)
    return ids


async def fail(db: AsyncSession, part: RefundPart) -> str:
    event = refund_event("refund.failed", f"rfnd_{part.id.hex[:12]}", str(part.id))
    return await settlement.handle_event(db, event, make_settings())


async def test_a_refund_that_fits_goes_to_the_wallet_as_before(db_session: AsyncSession) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await full_wallet(db_session, user, 600_000)

    entry = await wallet.refund_to_wallet(db_session, user, order, 100_000, "shortage")

    assert await wallet.balance(db_session, user) == 700_000
    assert "to_source" not in (entry.details or {})
    assert await refund_rows(db_session, user) == []
    assert await kinds(db_session, user) == ["topup", "refund"]


async def test_a_full_wallet_sends_the_whole_refund_to_the_payment_source(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    older, newer = await full_wallet(db_session, user, 600_000, 400_000)

    entry = await wallet.refund_to_wallet(db_session, user, order, 500_000, "shortage")

    (refund,) = await refund_rows(db_session, user)
    assert (refund.reason, refund.order_id, refund.amount_paise, refund.status) == (
        "order",
        order,
        500_000,
        "pending",
    )
    assert refund.idempotency_key == f"order:{order}:shortage"
    assert entry.details is not None and entry.details["to_source"] == str(refund.id)
    assert (entry.kind, entry.ref_type, entry.ref_id) == ("refund", "order", order)
    parts = await parts_of(db_session, refund)
    assert [(p.payment_id, p.amount_paise) for p in parts] == [(newer, 400_000), (older, 100_000)]
    assert await wallet.balance(db_session, user) == CAP  # in, then out again
    assert await wallet.in_flight_paise(db_session, user) == 500_000
    assert await kinds(db_session, user) == ["topup", "topup", "refund"] + ["refund_to_source"] * 2
    await assert_ledger_balances(db_session)


async def test_the_same_call_twice_makes_one_refund_and_no_second_entry(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await full_wallet(db_session, user, 600_000, 400_000)

    first = await wallet.refund_to_wallet(db_session, user, order, 500_000, "shortage")
    again = await wallet.refund_to_wallet(db_session, user, order, 500_000, "shortage")

    assert again.id == first.id
    assert len(await refund_rows(db_session, user)) == 1
    assert await kinds(db_session, user) == ["topup", "topup", "refund"] + ["refund_to_source"] * 2
    other = await wallet.refund_to_wallet(db_session, user, order, 1_000, "undelivered")
    assert other.id != first.id  # another reason is another refund
    assert len(await refund_rows(db_session, user)) == 2


async def test_top_ups_that_cannot_take_it_all_leave_wallet_limit_and_post_nothing(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await paid_topup(db_session, user, 600_000, age_days=181)  # past Razorpay's window
    await paid_topup(db_session, user, 400_000)

    with pytest.raises(ProblemError) as refused:
        await wallet.refund_to_wallet(db_session, user, order, 450_000, "shortage")

    assert (refused.value.status, refused.value.code) == (422, "billing.wallet_limit")
    assert refused.value.extra == {"room_paise": 0}
    assert await refund_rows(db_session, user) == []
    assert await kinds(db_session, user) == ["topup", "topup"]
    # A refund the fresh top-up can take still goes.
    await wallet.refund_to_wallet(db_session, user, order, 400_000, "undelivered")
    assert len(await refund_rows(db_session, user)) == 1


async def test_a_wallet_with_no_payment_to_go_back_to_keeps_wallet_limit(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await wallet.credit_topup(db_session, user, CAP, payment_ref="pay_x", topup_id=uuid.uuid4())

    with pytest.raises(ProblemError) as refused:
        await wallet.refund_to_wallet(db_session, user, order, 5_000, "shortage")

    assert refused.value.code == "billing.wallet_limit"
    assert await refund_rows(db_session, user) == []


async def test_under_one_rupee_on_a_full_wallet_is_wallet_limit(db_session: AsyncSession) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await full_wallet(db_session, user, 600_000, 400_000)

    with pytest.raises(ProblemError) as refused:
        await wallet.refund_to_wallet(db_session, user, order, 99, "shortage")

    assert refused.value.code == "billing.wallet_limit"
    assert await refund_rows(db_session, user) == []
    assert await kinds(db_session, user) == ["topup", "topup"]
    await wallet.refund_to_wallet(db_session, user, order, 100, "undelivered")  # ₹1 is enough
    assert len(await refund_rows(db_session, user)) == 1


async def test_an_order_refund_runs_beside_the_customers_own_open_refund(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await full_wallet(db_session, user, 600_000, 400_000)
    mine = await refunds.request_refund(
        db_session, user, 300_000, settings=make_settings(), idempotency_key="mine"
    )

    await wallet.refund_to_wallet(
        db_session, user, order, 100_000, "shortage"
    )  # 600k + 300k + 100k

    assert [r.reason for r in await refund_rows(db_session, user)] == ["customer", "order"]
    assert mine.status == "pending"
    # The customer's next request is still held back only by their own refund, not the order's.
    with pytest.raises(ProblemError) as held:
        await refunds.request_refund(
            db_session, user, 1_000, settings=make_settings(), idempotency_key="again"
        )
    assert held.value.extra == {"refund_id": str(mine.id)}
    await assert_ledger_balances(db_session)


async def test_an_order_refund_alone_does_not_hold_back_a_customer_request(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await full_wallet(db_session, user, 600_000, 400_000)
    await wallet.refund_to_wallet(db_session, user, order, 100_000, "shortage")

    mine = await refunds.request_refund(
        db_session, user, 200_000, settings=make_settings(), idempotency_key="mine"
    )

    assert mine.reason == "customer"
    await assert_ledger_balances(db_session)


async def test_a_failed_part_moves_once_to_another_top_up_then_is_unclaimed(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    _, b, c = await full_wallet(db_session, user, 500_000, 300_000, 200_000)
    await wallet.refund_to_wallet(db_session, user, order, 250_000, "shortage")
    (refund,) = await refund_rows(db_session, user)
    first, second = await parts_of(db_session, refund)
    assert [(first.payment_id, first.amount_paise), (second.payment_id, second.amount_paise)] == [
        (c, 200_000),
        (b, 50_000),
    ]

    assert await fail(db_session, first) == "refund_failed"  # back in: over the cap, so moved

    moved = [p for p in await parts_of(db_session, refund) if p.replaces_part_id == first.id]
    assert [(p.payment_id, p.amount_paise, p.status) for p in moved] == [(b, 200_000, "pending")]
    assert await wallet.balance(db_session, user) == CAP

    assert await fail(db_session, moved[0]) == "refund_failed"  # moved once already: unclaimed
    unclaimed = await service.platform_account(db_session, "unclaimed_balances")
    assert await service.balance(db_session, unclaimed) == 200_000
    assert await wallet.balance(db_session, user) == CAP

    done = await settlement.handle_event(
        db_session,
        refund_event("refund.processed", "rfnd_ok", str(second.id)),
        make_settings(),
    )
    assert done == "refund_processed"
    await db_session.refresh(refund)
    assert (refund.status, refund.settled_at is not None) == ("partly_failed", True)
    (event,) = await settled_events(db_session)
    assert event["order_id"] == str(order) and event["processed_paise"] == 50_000
    assert await kinds(db_session, user) == (
        ["topup"] * 3
        + ["refund"]
        + ["refund_to_source"] * 2
        + ["refund_to_source_failed", "refund_to_source"]  # first failed, moved
        + ["refund_to_source_failed", "unclaimed"]
    )
    await assert_ledger_balances(db_session)


async def test_a_failed_part_stays_in_the_wallet_when_it_fits_under_the_cap(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await full_wallet(db_session, user, 600_000, 400_000)
    await wallet.refund_to_wallet(db_session, user, order, 200_000, "shortage")
    (refund,) = await refund_rows(db_session, user)
    (part,) = await parts_of(db_session, refund)
    await wallet.debit_for_order(db_session, user, uuid.uuid4(), 300_000, date(2026, 10, 12))

    await fail(db_session, part)

    assert await wallet.balance(db_session, user) == CAP - 300_000 + 200_000
    assert len(await parts_of(db_session, refund)) == 1  # no move, no unclaimed
    assert (await kinds(db_session, user))[-2:] == ["box_debit", "refund_to_source_failed"]
    await db_session.refresh(refund)
    assert refund.status == "failed"
    await assert_ledger_balances(db_session)


async def test_the_customer_sees_the_order_refund_and_its_parts(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, user = await customer(api, google)
    topup = await dev_topup(api, auth, CAP)
    order = uuid.uuid4()
    await billing_api.refund_to_wallet(db_session, user, order, 50_000, "shortage")
    await db_session.commit()

    listed = (await api.get("/v1/wallet/refunds", headers=auth)).json()["items"]

    (shown,) = listed
    assert (shown["reason"], shown["order_id"], shown["amount_paise"]) == (
        "order",
        str(order),
        50_000,
    )
    assert [(p["topup_id"], p["amount_paise"]) for p in shown["parts"]] == [(topup, 50_000)]
    one = (await api.get(f"/v1/wallet/refunds/{shown['id']}", headers=auth)).json()
    assert one["order_id"] == str(order)
    wallet_body = (await api.get("/v1/wallet", headers=auth)).json()
    assert wallet_body["balance_paise"] == CAP
    refunded = wallet_body["entries"]
    assert [(e["kind"], e["amount_paise"]) for e in refunded[:2]] == [
        ("refund_to_source", -50_000),
        ("refund", 50_000),
    ]
    assert refunded[0]["ref"] == {"type": "refund", "id": shown["id"]}
    assert (refunded[1]["ref"]["type"], refunded[1]["ref"]["id"]) == ("order", str(order))

    month = (
        await api.get(f"/v1/statements?period={datetime.now(wallet.IST):%Y-%m}", headers=auth)
    ).json()
    assert [(e["kind"], e["amount_paise"]) for e in month["entries"][:2]] == [
        ("refund_to_source", -50_000),
        ("refund", 50_000),
    ]
    assert month["entries"][0]["balance_after_paise"] == CAP
    assert month["entries"][1]["balance_after_paise"] == CAP + 50_000

    await send(db_session)  # the dev part settles at once
    (event,) = await settled_events(db_session)
    assert (event["order_id"], event["status"]) == (str(order), "processed")
    # Asking for the customer's own refund later still works: the order's is settled anyway.
    assert (await ask(api, auth, 10_000)).status_code == 202


def _this_month() -> str:
    from datetime import datetime

    return datetime.now(wallet.IST).strftime("%Y-%m")
