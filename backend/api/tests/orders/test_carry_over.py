"""Carry-over (M4 §3.8, ADR-0027 §2 and §6): the non-perishable lines of a box missed twice are
kept as rows of their own, attached to the household's next paid box, followed through packing
and delivery, handed back when that box is missed too, and refunded without the fee after 14
days or when the hub loses them."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import JournalEntry, Refund
from gharbasket.modules.billing.wallet import CAP_PAISE, credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import deliveries
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import carry, service
from gharbasket.modules.orders.jobs import on_wallet_topped_up
from gharbasket.modules.orders.models import CarryOver, Order
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..billing.paid import paid_topup
from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_orders import PRIYA, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

MISSED = date(2026, 10, 13)  # Tuesday; the second miss is that evening
NEXT = date(2026, 10, 20)
LATER = date(2026, 10, 27)
THEN = datetime(2026, 10, 13, 15, 15, tzinfo=UTC)  # Tue 20:45 IST
# (steps, paid price per step): one perishable line, two that keep (toor dal and moong dal)
BOX = {"bhindi": (2, 2_000), "toor-dal": (2, 9_000), "moong-dal": (1, 7_000)}
KEPT = {"toor-dal": (2, 18_000), "moong-dal": (1, 7_000)}  # slug: (steps, paid)
KEPT_PAISE = 25_000
LOCKED = ("locked",)
AT_THE_DOOR = ("locked", "picking", "packed", "dispatched", "at_society", "undelivered")


def at(day: date, hour: int, minute: int) -> datetime:
    """UTC for an IST clock time."""
    total = hour * 60 + minute - 330
    return datetime.combine(day, time(), UTC) + timedelta(minutes=total)


async def place(
    db: AsyncSession,
    owner: uuid.UUID,
    delivery: date,
    reach: tuple[str, ...] = (),
    box: dict[str, tuple[int, int]] = BOX,
) -> uuid.UUID:
    """A box order for the owner's household, paid from the wallet and moved along `reach`."""
    household = await identity_api.household_for_user(db, owner)
    assert household is not None
    slot = await service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Carry {uuid.uuid4().hex[:8]}",
        kind="box",
        start=time(7),
        end=time(9),
        capacity=10,
    )
    ids = await catalog_api.product_ids_by_slug(db, list(box))
    order = await orders_api.create_from_cycle(
        db,
        orders_api.ApprovedCycle(
            cycle_id=uuid.uuid4(),
            household_id=household.id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            delivery_date=delivery,
            slot_id=slot.id,
            lines=tuple(orders_api.CycleLine(ids[s], q, p) for s, (q, p) in box.items()),
        ),
    )
    order_id, total = order.id, order.total_paise
    if reach:
        await credit_topup(db, owner, total, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4())
        await billing_api.debit_for_order(db, owner, order_id, total, delivery)
    for to in reach:
        await orders_api.transition(db, order_id, to, actor="system" if to == "locked" else "hub")
    await db.commit()
    return order_id


async def miss_twice(db: AsyncSession, order_id: uuid.UUID) -> list[CarryOver]:
    """M7's second miss: the perishables refunded minus the fee, the rest carried over."""
    order = await orders_api.get(db, order_id)
    amount, kept = await deliveries.second_miss_refund(db, order)
    await orders_api.transition(
        db,
        order_id,
        "refunded",
        actor="system",
        payload={"refund_paise": amount, "reason": "second_miss"},
    )
    rows = await orders_api.carry_over(db, order_id, kept)
    await db.commit()
    return rows


async def rows_of(db: AsyncSession, order_id: uuid.UUID) -> dict[str, CarryOver]:
    """The order's carry-over rows by product slug, read afresh."""
    found = await db.execute(
        select(CarryOver)
        .where(CarryOver.from_order_id == order_id)
        .execution_options(populate_existing=True)
    )
    slugs = {v: k for k, v in (await catalog_api.product_ids_by_slug(db, list(BOX))).items()}
    return {slugs[row.product_id]: row for row in found.scalars()}


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def job(db: AsyncSession, name: str, when: datetime) -> dict[str, Any]:
    run = await run_job(db, name, settings=make_settings(), scheduled_for=when)
    assert run.status == "ok", run.error
    assert run.summary is not None
    return run.summary


async def refund_keys(db: AsyncSession, order_id: uuid.UUID) -> set[str]:
    found = await db.execute(
        select(JournalEntry.idempotency_key).where(
            JournalEntry.ref_id == order_id, JournalEntry.kind == "refund"
        )
    )
    return set(found.scalars())


async def status_of(db: AsyncSession, order_id: uuid.UUID) -> str:
    return (await orders_api.get(db, order_id)).status


async def household(
    clients: ClientFactory, google: FakeGoogle
) -> tuple[Any, dict[str, str], uuid.UUID]:
    api = clients()
    auth, owner = await as_user(api, google, PRIYA)
    return api, auth, owner


async def test_a_second_miss_keeps_its_non_perishables_as_pending_rows_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, auth, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    ids = await catalog_api.product_ids_by_slug(db_session, list(BOX))

    with pytest.raises(ValueError):  # a box that was not refunded keeps nothing
        await orders_api.carry_over(db_session, missed, [])
    created = await miss_twice(db_session, missed)

    rows = await rows_of(db_session, missed)
    assert set(rows) == set(KEPT) and {r.id for r in created} == {r.id for r in rows.values()}
    for slug, (steps, paid) in KEPT.items():
        row = rows[slug]
        assert (row.status, row.steps, row.paid_paise) == ("pending", steps, paid)
        assert (row.step_qty, row.step_unit) == (500, "g")
        assert (row.missed_on, row.expires_on) == (MISSED, MISSED + timedelta(days=14))
        assert row.to_order_id is None and row.returned_in_order_id is None
        assert row.product_id == ids[slug]

    # Once per (order, product): the same call again returns the rows and changes nothing.
    again = await orders_api.carry_over(
        db_session,
        missed,
        [{"product_id": str(ids[s]), "steps": 1, "paid_paise": 1} for s in KEPT],
    )
    assert {r.id for r in again} == {r.id for r in created}
    assert (await rows_of(db_session, missed))["toor-dal"].paid_paise == 18_000
    with pytest.raises(ValueError):  # a product that was not on the order
        await orders_api.carry_over(
            db_session, missed, [{"product_id": str(uuid.uuid4()), "steps": 1, "paid_paise": 1}]
        )
    assert await events(db_session, "orders.carry_over_attached") == []  # no box to ride on yet

    # The missed order shows what became of them.
    detail = (await api.get(f"/v1/orders/{missed}", headers=auth)).json()
    assert detail["carry_over"] == {
        "status": "pending",
        "expires_on": "2026-10-27",
        "to_order_id": None,
    }
    assert detail["carried"] == []


async def test_rows_attach_at_the_next_lock_never_to_an_approved_box(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, auth, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    approved = await place(db_session, owner, NEXT)  # approved, not paid: never used
    await miss_twice(db_session, missed)
    assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"pending"}

    # The cut-off locks it (Mon 21:00 IST for Tuesday's box): the rows are attached.
    approved_total = (await orders_api.get(db_session, approved)).total_paise
    await credit_topup(
        db_session, owner, approved_total, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await db_session.commit()
    assert (await job(db_session, "cutoff_lock", at(NEXT - timedelta(days=1), 21, 0)))[
        "locked"
    ] == 1
    rows = await rows_of(db_session, missed)
    assert {(r.status, r.to_order_id) for r in rows.values()} == {("attached", approved)}
    [attached] = await events(db_session, "orders.carry_over_attached")
    assert attached["order_id"] == str(approved)
    assert {line["carry_id"] for line in attached["lines"]} == {str(r.id) for r in rows.values()}
    assert {line["carried_from_order_id"] for line in attached["lines"]} == {str(missed)}
    assert sorted(line["steps"] for line in attached["lines"]) == [1, 2]

    # A box the cut-off could not debit, paid by `pay` in the grace period, attaches too. This
    # miss is dated like the box above, so the box that is locked already is not the next one.
    second = await place(db_session, owner, LATER)
    again = await place(db_session, owner, NEXT, AT_THE_DOOR)
    await miss_twice(db_session, again)
    assert {r.status for r in (await rows_of(db_session, again)).values()} == {"pending"}
    eve = LATER - timedelta(days=1)
    assert (await job(db_session, "cutoff_lock", at(eve, 21, 0)))["payment_failed"] == 1
    total = (await orders_api.get(db_session, second)).total_paise
    await credit_topup(
        db_session, owner, total, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    with time_machine.travel(at(eve, 21, 30), tick=False):
        await service.pay(db_session, await service.get(db_session, second, for_update=True))
    await db_session.commit()
    later = await rows_of(db_session, again)
    assert {(r.status, r.to_order_id) for r in later.values()} == {("attached", second)}
    assert len(await events(db_session, "orders.carry_over_attached")) == 2
    assert {r.to_order_id for r in (await rows_of(db_session, missed)).values()} == {approved}

    # The customer sees them on the box that carries them, apart from its lines and total.
    detail = (await api.get(f"/v1/orders/{approved}", headers=auth)).json()
    toor = next(c for c in detail["carried"] if c["name"] == "Toor dal")
    assert toor == {
        "product_id": str(rows["toor-dal"].product_id),
        "name": "Toor dal",
        "steps": 2,
        "step_qty": 500,
        "step_unit": "g",
        "carried_from_order_id": str(missed),
    }
    assert len(detail["carried"]) == 2 and len(detail["lines"]) == len(BOX)
    assert detail["total_paise"] == approved_total  # nothing is charged for what rides along


async def test_a_box_already_locked_takes_the_rows_at_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    early = await place(db_session, owner, date(2026, 10, 12), LOCKED)  # before the miss
    next_box = await place(db_session, owner, NEXT, LOCKED)
    later = await place(db_session, owner, LATER, LOCKED)

    await miss_twice(db_session, missed)

    rows = await rows_of(db_session, missed)
    assert {(r.status, r.to_order_id) for r in rows.values()} == {("attached", next_box)}
    assert {early, later}.isdisjoint({r.to_order_id for r in rows.values()})
    [attached] = await events(db_session, "orders.carry_over_attached")
    assert attached["order_id"] == str(next_box) and len(attached["lines"]) == 2
    [summary] = await orders_api.summaries(db_session, [next_box])
    assert {c.product_id for c in summary.carried} == {r.product_id for r in rows.values()}
    assert {c.carried_from_order_id for c in summary.carried} == {missed}
    by_hub = await orders_api.orders_for_delivery(db_session, SEED_HUB_ID, NEXT)
    [mine] = [s for s in by_hub if s.order.id == next_box]
    assert sorted((c.steps, c.step_qty, c.step_unit) for c in mine.carried) == [
        (1, 500, "g"),
        (2, 500, "g"),
    ]
    # The paid lines of the same products are the box's own: carried items are rows apart.
    ids = await catalog_api.product_ids_by_slug(db_session, list(BOX))
    assert {ln.product_id: ln.qty for ln in mine.lines}[ids["toor-dal"]] == BOX["toor-dal"][0]


async def test_rows_follow_their_box_through_packing_and_delivery_without_touching_its_money(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, auth, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    next_box = await place(db_session, owner, NEXT, LOCKED)
    before = await orders_api.get(db_session, next_box)
    totals = (before.subtotal_paise, before.total_paise)
    lines_before = [
        (ln.product_id, ln.qty, ln.unit_price_paise)
        for ln in await service.lines_of(db_session, next_box)
    ]
    ids = await catalog_api.product_ids_by_slug(db_session, list(BOX))
    sold_before = await orders_api.sold_lines(db_session, SEED_HUB_ID, NEXT)

    await miss_twice(db_session, missed)

    assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"attached"}
    after = await orders_api.get(db_session, next_box)
    assert (after.subtotal_paise, after.total_paise) == totals  # nothing charged for them
    assert [
        (ln.product_id, ln.qty, ln.unit_price_paise)
        for ln in await service.lines_of(db_session, next_box)
    ] == lines_before
    assert await orders_api.sold_lines(db_session, SEED_HUB_ID, NEXT) == sold_before
    # A shortage on a carried product is the paid line's: the carried steps stay.
    await orders_api.mark_shortage(db_session, next_box, ids["toor-dal"], 1)
    assert (await rows_of(db_session, missed))["toor-dal"].steps == 2

    await orders_api.transition(db_session, next_box, "picking", actor="packer")
    # Still being picked: the hub can yet report an item lost, so it is not `packed` (M4 §3.8).
    assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"attached"}
    await orders_api.transition(db_session, next_box, "packed", actor="packer")
    assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"packed"}
    for to in ("dispatched", "at_society"):
        await orders_api.transition(db_session, next_box, to, actor="hub")
        assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"packed"}
    await orders_api.transition(db_session, next_box, "delivered", actor="hub")
    await db_session.commit()
    done = await rows_of(db_session, missed)
    assert {r.status for r in done.values()} == {"delivered"}
    assert all(r.settled_at is not None for r in done.values())
    # The carried items were delivered, so they are not refunded: only the perishables were.
    perishable = BOX["bhindi"][0] * BOX["bhindi"][1] - deliveries.SECOND_MISS_FEE_PAISE
    first = await orders_api.get(db_session, missed)
    assert await orders_api.refundable_paise(db_session, missed) == first.total_paise - perishable
    detail = (await api.get(f"/v1/orders/{missed}", headers=auth)).json()
    assert detail["carry_over"]["status"] == "delivered"
    assert detail["carry_over"]["to_order_id"] == str(next_box)

    # A hub that packs straight from locked (`locked -> packed`) packs them too.
    again = await place(db_session, owner, date(2026, 10, 14), AT_THE_DOOR)
    later = await place(db_session, owner, LATER, LOCKED)
    await miss_twice(db_session, again)
    assert {r.status for r in (await rows_of(db_session, again)).values()} == {"attached"}
    await orders_api.transition(db_session, later, "packed", actor="hub")
    assert {r.status for r in (await rows_of(db_session, again)).values()} == {"packed"}


async def test_rows_go_back_to_pending_when_their_box_is_missed_twice(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    next_box = await place(db_session, owner, NEXT, LOCKED)
    await miss_twice(db_session, missed)
    expires = {slug: r.expires_on for slug, r in (await rows_of(db_session, missed)).items()}
    for to in ("picking", "packed", "dispatched", "at_society", "undelivered", "redelivery"):
        await orders_api.transition(
            db_session, next_box, to, actor="system" if to == "redelivery" else "hub"
        )
    # Its first redelivery changes nothing.
    assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"packed"}
    for to in ("dispatched", "at_society", "undelivered"):
        await orders_api.transition(db_session, next_box, to, actor="hub")

    await miss_twice(db_session, next_box)  # its second miss: refunded, and its own rows carried

    back = await rows_of(db_session, missed)
    assert {(r.status, r.to_order_id, r.returned_in_order_id) for r in back.values()} == {
        ("pending", None, next_box)
    }
    assert {slug: r.expires_on for slug, r in back.items()} == expires  # the wait is not restarted
    # The second bag holds what rode in it and what it carried itself; the first bag's rows are
    # the same two, still waiting.
    in_bag = await orders_api.carried_lines(db_session, next_box)
    assert sorted((c.carried_from_order_id == missed, c.steps) for c in in_bag) == [
        (False, 1),
        (False, 2),
        (True, 1),
        (True, 2),
    ]
    assert {c.product_id for c in await orders_api.carried_lines(db_session, missed)} == {
        r.product_id for r in back.values()
    }
    # All of them wait for the household's next paid box and ride on it, oldest miss first.
    third = await place(db_session, owner, LATER, LOCKED)
    [*_, attached] = await events(db_session, "orders.carry_over_attached")
    assert attached["order_id"] == str(third) and len(attached["lines"]) == 4
    assert [line["carried_from_order_id"] for line in attached["lines"]][:2] == [str(missed)] * 2


async def test_the_expiry_job_refunds_the_paid_price_without_the_fee_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await miss_twice(db_session, missed)
    ids = {slug: r.product_id for slug, r in (await rows_of(db_session, missed)).items()}
    balance = await billing_api.balance(db_session, owner)

    # Nothing is due on the day the wait ends (Tue 27 Oct), only after it.
    assert await job(db_session, "expire_carry_overs", at(LATER, 2, 40)) == {
        "date": "2026-10-27",
        "refunded": 0,
        "refunded_paise": 0,
        "wallet_limit": 0,
        "no_owner": 0,
    }
    assert await billing_api.balance(db_session, owner) == balance

    after = at(LATER + timedelta(days=1), 2, 40)
    assert await job(db_session, "expire_carry_overs", after) == {
        "date": "2026-10-28",
        "refunded": 2,
        "refunded_paise": KEPT_PAISE,
        "wallet_limit": 0,
        "no_owner": 0,
    }
    assert await billing_api.balance(db_session, owner) == balance + KEPT_PAISE
    assert await refund_keys(db_session, missed) >= {
        f"refund:{missed}:carry_{ids[slug].hex}" for slug in KEPT
    }
    settled = await rows_of(db_session, missed)
    assert {(r.status, r.refunded_by) for r in settled.values()} == {("refunded", "expiry")}
    assert all(r.settled_at is not None for r in settled.values())
    [expired] = await events(db_session, "orders.carry_over_expired")
    assert expired["order_id"] == str(missed) and expired["reason"] == "carry_over_expired"
    assert {(line["carry_id"], line["paid_paise"]) for line in expired["lines"]} == {
        (str(settled[slug].id), paid) for slug, (_, paid) in KEPT.items()
    }

    # A repeat run, or a late one, moves no money and publishes nothing more.
    late = await job(db_session, "expire_carry_overs", after + timedelta(days=1))
    assert late["refunded"] == 0
    assert await billing_api.balance(db_session, owner) == balance + KEPT_PAISE
    assert len(await events(db_session, "orders.carry_over_expired")) == 1

    # With the perishables refunded minus the fee and the carried items refunded, the order has
    # got back what was paid for them and no more: only the fee and its own fees are left.
    order = await orders_api.get(db_session, missed)
    perishable = BOX["bhindi"][0] * BOX["bhindi"][1] - deliveries.SECOND_MISS_FEE_PAISE
    left = await orders_api.refundable_paise(db_session, missed)
    assert left == order.total_paise - perishable - KEPT_PAISE
    assert left >= deliveries.SECOND_MISS_FEE_PAISE


async def test_the_expiry_job_skips_attached_rows_and_leaves_a_wallet_at_its_cap(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    # A's rows ride on the box B; P's miss is dated like B, so it is not B's to carry: pending.
    a = await place(db_session, owner, date(2026, 10, 12), AT_THE_DOOR)
    b = await place(db_session, owner, date(2026, 10, 14), LOCKED)
    await miss_twice(db_session, a)
    p = await place(db_session, owner, date(2026, 10, 14), AT_THE_DOOR)
    await miss_twice(db_session, p)
    assert {r.status for r in (await rows_of(db_session, a)).values()} == {"attached"}
    assert {r.status for r in (await rows_of(db_session, p)).values()} == {"pending"}

    # The wallet is full: the refund would pass the cap, so P's rows wait for the next run.
    room = CAP_PAISE - await billing_api.balance(db_session, owner)
    await credit_topup(db_session, owner, room, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4())
    await db_session.commit()
    late = at(date(2026, 11, 3), 2, 40)
    assert await job(db_session, "expire_carry_overs", late) == {
        "date": "2026-11-03",
        "refunded": 0,
        "refunded_paise": 0,
        "wallet_limit": 2,  # A's rows are on a paid box: not tried at all
        "no_owner": 0,
    }
    assert {r.status for r in (await rows_of(db_session, p)).values()} == {"pending"}
    assert {r.status for r in (await rows_of(db_session, a)).values()} == {"attached"}
    assert await events(db_session, "orders.carry_over_expired") == []

    # Packed on the box: still not refunded. Room again: the next run takes P's rows.
    await orders_api.transition(db_session, b, "picking", actor="packer")
    await orders_api.transition(db_session, b, "packed", actor="packer")
    await billing_api.debit_for_order(db_session, owner, uuid.uuid4(), KEPT_PAISE, NEXT)
    await db_session.commit()
    next_run = await job(db_session, "expire_carry_overs", late + timedelta(days=1))
    assert (next_run["refunded"], next_run["wallet_limit"]) == (2, 0)
    assert {r.status for r in (await rows_of(db_session, p)).values()} == {"refunded"}
    assert {r.status for r in (await rows_of(db_session, a)).values()} == {"packed"}


async def test_the_expiry_job_refunds_a_full_wallet_to_the_payment_source(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await miss_twice(db_session, missed)
    await paid_topup(db_session, owner, CAP_PAISE - await billing_api.balance(db_session, owner))
    await db_session.commit()

    summary = await job(db_session, "expire_carry_overs", at(LATER + timedelta(days=1), 2, 40))

    assert (summary["refunded"], summary["refunded_paise"], summary["wallet_limit"]) == (
        2,
        KEPT_PAISE,
        0,
    )
    assert await billing_api.balance(db_session, owner) == CAP_PAISE  # the money went out again
    sent = list(
        (await db_session.execute(select(Refund).where(Refund.order_id == missed))).scalars()
    )
    assert [r.reason for r in sent] == ["order", "order"]
    assert sum(r.amount_paise for r in sent) == KEPT_PAISE
    settled = await rows_of(db_session, missed)
    assert {(r.status, r.refunded_by) for r in settled.values()} == {("refunded", "expiry")}


async def test_refund_carry_refunds_once_and_refuses_a_packed_or_delivered_row(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await miss_twice(db_session, missed)
    rows = await rows_of(db_session, missed)
    toor = rows["toor-dal"]
    balance = await billing_api.balance(db_session, owner)

    lost = await orders_api.refund_carry(db_session, toor.id, uuid.uuid4())
    assert (lost.status, lost.refunded_by) == ("refunded", "hub") and lost.settled_at is not None
    assert await billing_api.balance(db_session, owner) == balance + 18_000
    assert f"refund:{missed}:carry_{toor.product_id.hex}" in await refund_keys(db_session, missed)
    again = await orders_api.refund_carry(db_session, toor.id)  # as is: no money moves
    assert again.id == toor.id and again.status == "refunded"
    assert await billing_api.balance(db_session, owner) == balance + 18_000
    assert [c.product_id for c in await orders_api.carried_lines(db_session, missed)] == [
        rows["moong-dal"].product_id
    ]

    # An attached row can be refunded too; then it no longer rides on its box.
    box = await place(db_session, owner, NEXT, LOCKED)
    assert (await rows_of(db_session, missed))["moong-dal"].to_order_id == box
    assert (await orders_api.refund_carry(db_session, rows["moong-dal"].id)).status == "refunded"
    [summary] = await orders_api.summaries(db_session, [box])
    assert summary.carried == []

    # A row on a box that is packed, or was delivered, is not refundable.
    other = await place(db_session, owner, date(2026, 10, 14), AT_THE_DOOR)
    await miss_twice(db_session, other)
    stuck = (await rows_of(db_session, other))["toor-dal"]
    assert (stuck.status, stuck.to_order_id) == ("attached", box)
    await orders_api.transition(db_session, box, "picking", actor="packer")
    # While the box is being picked the hub can still refund (M6 takes the item off the task).
    picking = (await rows_of(db_session, other))["moong-dal"]
    assert (await orders_api.refund_carry(db_session, picking.id)).status == "refunded"
    await orders_api.transition(db_session, box, "packed", actor="packer")
    for expected, to in (("packed", None), ("delivered", "delivered")):
        if to is not None:
            for step in ("dispatched", "at_society", "delivered"):
                await orders_api.transition(db_session, box, step, actor="hub")
        with pytest.raises(ProblemError) as refused:
            await orders_api.refund_carry(db_session, stuck.id)
        assert (refused.value.status, refused.value.code) == (409, "orders.carry_not_refundable")
        assert refused.value.extra == {"current_status": expected}
    with pytest.raises(ProblemError) as unknown:
        await orders_api.refund_carry(db_session, uuid.uuid4())
    assert unknown.value.status == 404


async def test_the_purge_waits_while_a_row_is_open_and_the_export_lists_them(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    home = await identity_api.household_for_user(db_session, owner)
    assert home is not None
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    assert await orders_api.has_open_orders(db_session, home.id) is True  # undelivered
    await miss_twice(db_session, missed)
    # Every order is final, but two rows still wait: the purge waits for them.
    assert await status_of(db_session, missed) == "refunded"
    assert await orders_api.has_open_orders(db_session, home.id) is True

    exported = await orders_api.export_for_user(db_session, owner)
    assert sorted(r["status"] for r in exported["carry_overs"]) == ["pending", "pending"]
    assert {r["from_order_id"] for r in exported["carry_overs"]} == {missed}
    assert all("household_id" not in r for r in exported["carry_overs"])

    for row in (await rows_of(db_session, missed)).values():
        await orders_api.refund_carry(db_session, row.id)
    assert await orders_api.has_open_orders(db_session, home.id) is False


async def test_a_top_up_in_the_grace_period_attaches_the_rows(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    unpaid = await place(db_session, owner, NEXT)
    await miss_twice(db_session, missed)
    eve = NEXT - timedelta(days=1)
    assert (await job(db_session, "cutoff_lock", at(eve, 21, 0)))["payment_failed"] == 1
    assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"pending"}

    total = (await orders_api.get(db_session, unpaid)).total_paise
    await credit_topup(
        db_session, owner, total, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    with time_machine.travel(at(eve, 21, 30), tick=False):
        await on_wallet_topped_up(
            db_session,
            OutboxEvent(event_type="billing.wallet_topped_up", payload={"user_id": str(owner)}),
        )
    await db_session.commit()

    assert (await orders_api.get(db_session, unpaid)).status == "locked"
    rows = await rows_of(db_session, missed)
    assert {(r.status, r.to_order_id) for r in rows.values()} == {("attached", unpaid)}
    [attached] = await events(db_session, "orders.carry_over_attached")
    assert attached["order_id"] == str(unpaid)


async def test_a_tiffin_never_takes_or_gives_back_carried_items(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await miss_twice(db_session, missed)
    tiffin = await place(db_session, owner, NEXT)
    await db_session.execute(update(Order).where(Order.id == tiffin).values(kind="tiffin"))
    total = (await orders_api.get(db_session, tiffin)).total_paise
    await credit_topup(
        db_session, owner, total, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await billing_api.debit_for_order(db_session, owner, tiffin, total, NEXT)
    await db_session.commit()

    await orders_api.transition(db_session, tiffin, "locked", actor="system")
    assert {r.status for r in (await rows_of(db_session, missed)).values()} == {"pending"}
    assert await events(db_session, "orders.carry_over_attached") == []
    # Even a row that rides on a box is not given back by a tiffin's refund.
    await orders_api.transition(
        db_session,
        tiffin,
        "refunded",
        actor="system",
        payload={"refund_paise": 0, "reason": "no_kitchen"},
    )
    assert {
        (r.status, r.returned_in_order_id) for r in (await rows_of(db_session, missed)).values()
    } == {("pending", None)}


async def test_rows_ride_on_the_next_locked_box_when_their_box_is_refunded_otherwise(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    b = await place(db_session, owner, NEXT, LOCKED)
    await miss_twice(db_session, missed)
    c = await place(db_session, owner, LATER, LOCKED)
    assert {r.to_order_id for r in (await rows_of(db_session, missed)).values()} == {b}
    for to in ("picking", "packed", "dispatched", "at_society", "undelivered"):
        await orders_api.transition(db_session, b, to, actor="hub")

    # A triage refund (not a second miss) of the box that carries them: C is already locked.
    await orders_api.transition(
        db_session, b, "refunded", actor="system", payload={"refund_paise": 0, "reason": "triage"}
    )
    await db_session.commit()

    rows = await rows_of(db_session, missed)
    assert {(r.status, r.to_order_id, r.returned_in_order_id) for r in rows.values()} == {
        ("attached", c, b)
    }
    [*_, attached] = await events(db_session, "orders.carry_over_attached")
    assert attached["order_id"] == str(c) and len(attached["lines"]) == 2


async def test_a_carry_refund_is_never_more_than_is_left_on_the_missed_order(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await miss_twice(db_session, missed)
    rows = await rows_of(db_session, missed)
    balance = await billing_api.balance(db_session, owner)

    async def room(_: AsyncSession, __: Order) -> int:
        return 5_000

    monkeypatch.setattr(service, "refundable_paise", room)
    capped = await orders_api.refund_carry(db_session, rows["toor-dal"].id)  # paid 18,000
    assert capped.status == "refunded"
    assert await billing_api.balance(db_session, owner) == balance + 5_000

    async def none(_: AsyncSession, __: Order) -> int:
        return 0

    monkeypatch.setattr(service, "refundable_paise", none)
    settled = await orders_api.refund_carry(db_session, rows["moong-dal"].id)
    assert (settled.status, settled.settled_at is not None) == ("refunded", True)
    assert await billing_api.balance(db_session, owner) == balance + 5_000  # nothing was left
    assert await carry.refunded_paise(db_session, missed) == KEPT_PAISE  # both rows are settled


async def test_a_household_without_an_owner_is_counted_and_the_others_still_refunded(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await miss_twice(db_session, missed)
    ordered = sorted((await rows_of(db_session, missed)).values(), key=lambda r: r.id)
    balance = await billing_api.balance(db_session, owner)
    real = identity_api.owner_of_household
    calls = {"n": 0}

    async def owner_of(session: AsyncSession, household_id: uuid.UUID) -> uuid.UUID | None:
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(session, household_id)

    monkeypatch.setattr(identity_api, "owner_of_household", owner_of)

    summary = await job(db_session, "expire_carry_overs", at(LATER + timedelta(days=1), 2, 40))

    assert (summary["refunded"], summary["no_owner"], summary["wallet_limit"]) == (1, 1, 0)
    assert summary["refunded_paise"] == ordered[1].paid_paise
    assert await billing_api.balance(db_session, owner) == balance + ordered[1].paid_paise
    left = {r.id: r.status for r in (await rows_of(db_session, missed)).values()}
    assert left == {ordered[0].id: "pending", ordered[1].id: "refunded"}
