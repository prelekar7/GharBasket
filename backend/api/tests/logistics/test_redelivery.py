"""Hold expiry and redeliveries (M7 §3.4, §6; ADR-0027): a held bag nobody collected becomes
undelivered at 20:30 and gets one free redelivery the next morning, staying with the captain
overnight (her society still gets a stop; the bag is not counted yet moves at the handover);
bags back at the hub ride the next van; a second miss refunds the perishables minus ₹20."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.models import JournalEntry, Posting
from gharbasket.modules.billing.wallet import CAP_PAISE, credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.logistics import attempts, deliveries
from gharbasket.modules.logistics.models import Delivery, FieldEvent, Route, RouteStop
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import CarryOver, Order
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    MEENA,
    MORNING,
    PRIYA,
    RAMESH,
    SUNITA,
    VIKRAM,
    as_user,
    key,
    pack,
    published_day,
    user_id,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")
NEXT_DAY = date(2026, 10, 14)


def at(day: int, hour: int, minute: int) -> datetime:
    """UTC for an IST clock time in October 2026."""
    total = hour * 60 + minute - 330
    return datetime(2026, 10, day, 0, 0, tzinfo=UTC).replace(hour=total // 60, minute=total % 60)


async def job(db: AsyncSession, name: str, when: datetime) -> dict[str, Any]:
    run = await run_job(db, name, settings=make_settings(), scheduled_for=when)
    assert run.status == "ok", run.error
    assert run.summary is not None
    return run.summary


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def attempts_of(db: AsyncSession, order_id: uuid.UUID) -> list[Delivery]:
    rows = await db.execute(
        select(Delivery)
        .where(Delivery.order_id == order_id)
        .order_by(Delivery.attempt)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def stops(db: AsyncSession, route_id: uuid.UUID) -> dict[uuid.UUID | None, RouteStop]:
    rows = await db.execute(
        select(RouteStop)
        .where(RouteStop.route_id == route_id)
        .execution_options(populate_existing=True)
    )
    return {s.society_id: s for s in rows.scalars()}


async def status(db: AsyncSession, order_id: uuid.UUID) -> str:
    found = await db.scalar(
        select(Order.status).where(Order.id == order_id).execution_options(populate_existing=True)
    )
    assert found is not None
    return found


async def test_a_held_bag_is_redelivered_from_the_captain_and_a_second_miss_is_refunded(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day = await published_day(db_session)
        await pack(db_session, *day.orders.values())
        rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
        started = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
        assert started.status_code == 200, started.text
        first = {
            s: (stop.id, stop.handover_code)
            for s, stop in (await stops(db_session, day.route_id)).items()
        }
        sunrise = first[SEED_SOCIETY_ID]
        handed = await api.post(
            f"/v1/stops/{sunrise[0]}/handover",
            json={"crates": 1, "bags": 2, "handover_code": sunrise[1]},
            headers={**captain, **key()},
        )
        assert handed.status_code == 200, handed.text
        meena_order, priya_order = day.orders[MEENA], day.orders[PRIYA]
        held = await api.post(
            f"/v1/deliveries/{meena_order}/undelivered",
            json={"reason": "not_home"},
            headers={**captain, **key()},
        )
        assert held.json()["status"] == "held"
        priya_done = await api.post(
            f"/v1/deliveries/{priya_order}/proof",
            json={"bag_qr": f"GB1:{priya_order.hex}", "proof": "scan"},
            headers={**captain, **key()},
        )
        assert priya_done.status_code == 200
        for society, (stop_id, _) in first.items():
            if society != SEED_SOCIETY_ID:
                skipped = await api.post(
                    f"/v1/stops/{stop_id}/skip",
                    json={"reason": "no_access"},
                    headers={**rider, **key()},
                )
                assert skipped.status_code == 200
        done = await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
        assert done.status_code == 200, done.text

    # 20:30: the held bag nobody collected becomes undelivered (not home); 20:45: redeliveries.
    assert await job(db_session, "hold_expiry", at(13, 20, 30)) == {
        "date": "2026-10-13",
        "not_home": 1,
        "not_confirmed": 0,
        "failed": 0,
        "left_behind": 0,
    }
    rerun = await job(db_session, "hold_expiry", at(13, 20, 30))
    assert (rerun["not_home"], rerun["not_confirmed"]) == (0, 0)  # nothing twice
    assert await status(db_session, meena_order) == "undelivered"
    assert await job(db_session, "schedule_redeliveries", at(13, 20, 45)) == {
        "date": "2026-10-13",
        "redelivery": 3,
        "refunded": 0,
        "triage": 0,
        "failed": 0,
    }
    again = {
        d.order_id: d
        for d in (await db_session.execute(select(Delivery).where(Delivery.attempt == 2))).scalars()
    }
    assert {o: (d.delivery_date, d.kept_by_captain) for o, d in again.items()} == {
        meena_order: (NEXT_DAY, True),
        day.orders[RAMESH]: (NEXT_DAY, False),
        day.orders[SUNITA]: (NEXT_DAY, False),
    }
    assert await status(db_session, meena_order) == "redelivery"
    # Running it again changes nothing.
    assert (await job(db_session, "schedule_redeliveries", at(13, 20, 45)))["redelivery"] == 0

    # T+1's 23:15 solve gives the three societies a stop; the kept bag weighs nothing on the van.
    solve = await job(db_session, "solve_routes", at(13, 23, 15))
    assert (solve["delivery_date"], solve["published"]) == ("2026-10-14", 1), solve
    route_id = await db_session.scalar(select(Route.id).where(Route.delivery_date == NEXT_DAY))
    assert route_id is not None
    planned = await stops(db_session, route_id)
    assert set(planned) == {
        SEED_SOCIETY_ID,
        GREEN_VALLEY,
        *[s for s in first if s not in (SEED_SOCIETY_ID, GREEN_VALLEY)],
    }
    assert planned[SEED_SOCIETY_ID].crates == 0

    with time_machine.travel(at(14, 6, 30), tick=False):
        rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
        started = await api.post(f"/v1/routes/{route_id}/start", headers={**rider, **key()})
        assert started.status_code == 200, started.text
        by_society = {s["society"]["id"]: s for s in started.json()["stops"]}
        assert by_society[str(SEED_SOCIETY_ID)]["redelivery_bags"] == 0  # she kept it
        assert by_society[str(GREEN_VALLEY)]["redelivery_bags"] == 1
        assert await status(db_session, meena_order) == "dispatched"
        flats = (await api.get("/v1/captains/me/today", headers=captain)).json()["flats"]
        [kept] = [f for f in flats if f["order_id"] == str(meena_order)]
        assert (kept["redelivery"], kept["status"]) == (True, "on_the_way")
        today = await stops(db_session, route_id)
        sunrise_id, code = today[SEED_SOCIETY_ID].id, today[SEED_SOCIETY_ID].handover_code
        handed = await api.post(
            f"/v1/stops/{sunrise_id}/handover",
            json={"crates": 0, "bags": 0, "handover_code": code},
            headers={**captain, **key()},
        )
        assert handed.status_code == 200, handed.text
        assert handed.json()["at_society"] == [str(meena_order)]
        delivered = await api.post(
            f"/v1/deliveries/{meena_order}/proof",
            json={"bag_qr": f"GB1:{meena_order.hex}", "proof": "scan"},
            headers={**captain, **key()},
        )
        assert delivered.status_code == 200 and delivered.json()["attempt"] == 2
        for society, stop in today.items():
            if society != SEED_SOCIETY_ID:
                await api.post(
                    f"/v1/stops/{stop.id}/skip",
                    json={"reason": "no_access"},
                    headers={**rider, **key()},
                )
        done = await api.post(f"/v1/routes/{route_id}/complete", headers={**rider, **key()})
        assert done.status_code == 200, done.text

    # The second miss (the owner's decision of 9 Oct): the perishables minus ₹20, never below 0;
    # the non-perishables are not refunded, they ride on the household's next delivery. The seed
    # box has one perishable line, so tomato counts as one too here.
    ramesh = day.orders[RAMESH]
    [summary] = await orders_api.summaries(db_session, [ramesh])
    real = catalog_api.perishable_ids
    tomato = (await catalog_api.product_ids_by_slug(db_session, ["tomato"]))["tomato"]

    async def tomato_too(session: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return await real(session, ids) | {tomato}

    monkeypatch.setattr(catalog_api, "perishable_ids", tomato_too)
    fresh = await tomato_too(db_session, [ln.product_id for ln in summary.lines])
    paid = {True: 0, False: 0}
    for ln in summary.lines:
        paid[ln.product_id in fresh] += (ln.qty - ln.shortage_qty) * ln.unit_price_paise
    expected = max(0, paid[True] - 2_000)
    assert 0 < expected < summary.order.total_paise and paid[False] > 0
    assert await job(db_session, "schedule_redeliveries", at(14, 20, 45)) == {
        "date": "2026-10-14",
        "redelivery": 0,
        "refunded": 2,
        "triage": 0,
        "failed": 0,
    }
    assert await status(db_session, ramesh) == "refunded"
    # Refunded attempts ride no more: the stops count only the live redelivery (Meena's).
    stop_ids = [stop.id for stop in (await stops(db_session, route_id)).values()]
    riding = await attempts.riding(db_session, stop_ids)
    assert [(d.order_id, d.status) for d in riding] == [(meena_order, "delivered")]
    second = await db_session.scalar(
        select(Delivery)
        .where(Delivery.order_id == ramesh, Delivery.attempt == 2)
        .execution_options(populate_existing=True)
    )
    assert second is not None and second.refund_paise == expected
    entry = await db_session.scalar(
        select(JournalEntry.id).where(
            JournalEntry.idempotency_key == f"refund:{ramesh}:second_miss"
        )
    )
    postings = await db_session.execute(select(Posting.amount).where(Posting.entry_id == entry))
    assert sorted(postings.scalars()) == [-expected, expected]  # the ledger paid exactly that
    [tap] = (
        await db_session.execute(
            select(FieldEvent.payload).where(
                FieldEvent.order_id == ramesh, FieldEvent.action == "resolve"
            )
        )
    ).scalars()
    assert tap is not None and tap["refund_paise"] == expected
    kept = {str(ln.product_id) for ln in summary.lines if ln.product_id not in fresh}
    assert {line["product_id"] for line in tap["non_perishable"]} == kept
    # What was not refunded stays visible: the order has the rest of its paid total unrefunded and
    # the carry-over record is published as M7's own record (M4 keeps the rows, M6 the goods).
    assert await orders_api.refundable_paise(db_session, ramesh) == paid[False] + 2_000
    [pending] = [
        e
        for e in await events(db_session, "logistics.carry_over_pending")
        if e["order_id"] == str(ramesh)
    ]
    assert {line["product_id"] for line in pending["lines"]} == kept
    assert sum(line["paid_paise"] for line in pending["lines"]) == paid[False]
    # M4 keeps them in the same transaction (M4 §3.8): a pending row per line, until the
    # household's next box (none here) or 14 days after the miss.
    carried = list(
        (
            await db_session.execute(
                select(CarryOver)
                .where(CarryOver.from_order_id == ramesh)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    assert {str(row.product_id) for row in carried} == kept
    assert {row.status for row in carried} == {"pending"}
    assert sum(row.paid_paise for row in carried) == paid[False]
    assert {row.missed_on for row in carried} == {summary.order.delivery_date}
    assert {(row.expires_on - row.missed_on).days for row in carried} == {14}
    assert {c.product_id for c in await orders_api.carried_lines(db_session, ramesh)} == {
        row.product_id for row in carried
    }


async def test_hold_expiry_sends_unconfirmed_bags_to_triage(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day = await published_day(db_session)
        await pack(db_session, *day.orders.values())
        rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
        assert (
            await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
        ).status_code == 200
        sunrise = (await stops(db_session, day.route_id))[SEED_SOCIETY_ID]
        sunrise_id, code = sunrise.id, sunrise.handover_code
        assert (
            await api.post(
                f"/v1/stops/{sunrise_id}/handover",
                json={"crates": 1, "bags": 2, "handover_code": code},
                headers={**captain, **key()},
            )
        ).status_code == 200
    summary = await job(db_session, "hold_expiry", at(13, 20, 30))
    assert (summary["not_home"], summary["not_confirmed"]) == (0, 2)
    rows = await db_session.execute(
        select(Delivery.status, Delivery.reason).where(
            Delivery.order_id.in_([day.orders[PRIYA], day.orders[MEENA]])
        )
    )
    assert set(rows.all()) == {("triage", "not_confirmed")}
    # Triage reasons are the hub's, not the evening job's.
    assert (await job(db_session, "schedule_redeliveries", at(13, 20, 45)))["redelivery"] == 0


@time_machine.travel(MORNING, tick=False)
async def test_the_fee_floors_the_perishables_at_zero_and_the_non_perishables_are_not_refunded(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = await published_day(db_session)
    order = await orders_api.get(db_session, day.orders[RAMESH])
    [summary] = await orders_api.summaries(db_session, [order.id])
    cheapest = min(summary.lines, key=lambda ln: ln.qty * ln.unit_price_paise)
    cheapest.unit_price_paise = 400  # 2 steps: ₹8 of perishables, less than the ₹20 fee
    await db_session.flush()
    assert cheapest.qty * cheapest.unit_price_paise < 2_000

    async def only_the_cheapest(*_: object) -> set[uuid.UUID]:
        return {cheapest.product_id}

    monkeypatch.setattr(catalog_api, "perishable_ids", only_the_cheapest)
    amount, kept = await deliveries.second_miss_refund(db_session, order)
    assert amount == 0  # the perishable part is floored at 0; the rest is not refunded
    assert len(kept) == len(summary.lines) - 1
    assert sum(int(str(line["paid_paise"])) for line in kept) > 0


async def test_one_failing_order_is_counted_and_a_full_wallet_goes_to_triage(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day = await published_day(db_session)
        await pack(db_session, *day.orders.values())
        rider = await as_user(api, google, ARJUN)
        assert (
            await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
        ).status_code == 200
        for stop in (await stops(db_session, day.route_id)).values():
            await api.post(
                f"/v1/stops/{stop.id}/skip",
                json={"reason": "no_access"},
                headers={**rider, **key()},
            )
        assert (
            await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
        ).status_code == 200
    # Four returned bags; the move of Ramesh's breaks: the other three still get redeliveries.
    real = orders_api.transition

    async def flaky(session: AsyncSession, order_id: uuid.UUID, *args: Any, **kw: Any) -> Any:
        if order_id == day.orders[RAMESH]:
            raise RuntimeError("boom")
        return await real(session, order_id, *args, **kw)

    monkeypatch.setattr(orders_api, "transition", flaky)
    summary = await job(db_session, "schedule_redeliveries", at(13, 20, 45))
    assert (summary["redelivery"], summary["failed"]) == (3, 1)
    monkeypatch.setattr(
        orders_api, "transition", real
    )  # not `undo`: it would drop the settings too
    assert await status(db_session, day.orders[RAMESH]) == "undelivered"

    # A second miss whose refund the wallet cannot take goes to triage (all of it perishable here,
    # so there is a refund to take).
    async def all_fresh(_: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return set(ids)

    monkeypatch.setattr(catalog_api, "perishable_ids", all_fresh)
    priya = day.orders[PRIYA]
    await db_session.execute(
        update(Delivery)
        .where(Delivery.order_id == priya, Delivery.attempt == 2)
        .values(status="undelivered", reason="returned_to_hub", delivery_date=date(2026, 10, 13))
    )
    await db_session.execute(update(Order).where(Order.id == priya).values(status="undelivered"))
    owner = await user_id(db_session, PRIYA)
    await credit_topup(
        db_session, owner, CAP_PAISE - 100, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await db_session.commit()
    again = await job(db_session, "schedule_redeliveries", at(13, 20, 45))
    assert (again["triage"], again["refunded"]) == (1, 0)
    second = await db_session.scalar(
        select(Delivery.status).where(Delivery.order_id == priya, Delivery.attempt == 2)
    )
    assert second == "triage"


async def test_left_behind_bags_go_to_triage_at_day_end_and_can_ride_again(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day = await published_day(db_session)
        meena_order = day.orders[MEENA]
        await pack(db_session, *[o for p, o in day.orders.items() if p != MEENA])
        rider = await as_user(api, google, ARJUN)
        started = await api.post(
            f"/v1/routes/{day.route_id}/start",
            json={"leave_behind": True},
            headers={**rider, **key()},
        )
        assert started.status_code == 200
        await pack(db_session, meena_order)  # packed later that morning; the van had gone
    summary = await job(db_session, "hold_expiry", at(13, 20, 30))
    assert summary["left_behind"] == 1
    assert (await job(db_session, "hold_expiry", at(13, 20, 30)))["left_behind"] == 0
    [attempt] = (
        await db_session.execute(select(Delivery).where(Delivery.order_id == meena_order))
    ).scalars()
    assert (attempt.status, attempt.reason) == ("triage", "left_behind")
    with time_machine.travel(at(13, 21, 0), tick=False):
        manager = await as_user(api, google, VIKRAM)
        refund = await api.post(
            f"/v1/deliveries/{meena_order}/resolve",
            json={"outcome": "refund", "note": "x"},
            headers={**manager, **key()},
        )
        assert (refund.status_code, refund.json()["code"]) == (409, "logistics.not_undelivered")
        ride = await api.post(
            f"/v1/deliveries/{meena_order}/resolve",
            json={"outcome": "redeliver", "note": "tomorrow's van"},
            headers={**manager, **key()},
        )
        assert ride.status_code == 200, ride.text
        # The ride is attempt 1 booked again, not the free redelivery (that is still unused).
        assert (ride.json()["attempt"], ride.json()["delivery_date"]) == (1, "2026-10-14")
        assert ride.json()["status"] == "scheduled"
    assert await status(db_session, meena_order) == "packed"
    [booked] = await attempts_of(db_session, meena_order)
    assert (booked.hub_ride, booked.status, booked.delivery_date) == (True, "scheduled", NEXT_DAY)
