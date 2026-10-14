"""Hold expiry and redeliveries (M7 §3.4, §6; ADR-0027): a held bag nobody collected becomes
undelivered at 20:30 and gets one free redelivery the next morning, staying with the captain
overnight (her society still gets a stop; the bag is not counted yet moves at the handover);
bags back at the hub ride the next van; a second miss refunds the perishables minus ₹20."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.logistics.models import Delivery, Route, RouteStop
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import Order
from gharbasket.platform.jobs import run_job
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
    as_user,
    key,
    pack,
    published_day,
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
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
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
    }
    assert await status(db_session, meena_order) == "undelivered"
    assert await job(db_session, "schedule_redeliveries", at(13, 20, 45)) == {
        "date": "2026-10-13",
        "redelivery": 3,
        "refunded": 0,
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

    # The second miss: the perishables are refunded minus ₹20; the rest is not refunded.
    ramesh = day.orders[RAMESH]
    [summary] = await orders_api.summaries(db_session, [ramesh])
    fresh = await catalog_api.perishable_ids(db_session, [ln.product_id for ln in summary.lines])
    paid = sum(
        (ln.qty - ln.shortage_qty) * ln.unit_price_paise
        for ln in summary.lines
        if ln.product_id in fresh
    )
    assert await job(db_session, "schedule_redeliveries", at(14, 20, 45)) == {
        "date": "2026-10-14",
        "redelivery": 0,
        "refunded": 2,
    }
    assert await status(db_session, ramesh) == "refunded"
    second = await db_session.scalar(
        select(Delivery)
        .where(Delivery.order_id == ramesh, Delivery.attempt == 2)
        .execution_options(populate_existing=True)
    )
    assert second is not None and second.refund_paise == max(0, paid - 2_000)


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
