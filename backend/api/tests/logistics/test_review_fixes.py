"""The Opus review's findings on the M7 chain: stale locked reads, proof photos kept and used once,
a left-behind ride that is no customer miss, orphan redeliveries, bags in the captain's hands, the
customer watching a redelivery, a second miss that leaves the non-perishables alone, and small
edges (a route's last position, older undelivered attempts, a concurrent resolve)."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
import time_machine
from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import Refund
from gharbasket.modules.billing.wallet import CAP_PAISE
from gharbasket.modules.logistics import attempts, execution, live, tracking
from gharbasket.modules.logistics.models import Delivery, Handover, Route, RouteStop
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent, Upload
from gharbasket.platform.permissions import Grant, Permit
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..billing.paid import paid_topup
from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..test_files import SETTINGS as STORAGE_SETTINGS
from ..test_files import FakeS3
from .conftest import (
    ARJUN,
    DAY,
    MEENA,
    MORNING,
    PRIYA,
    RAMESH,
    SUNITA,
    VIKRAM,
    Day,
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


def bag(order_id: uuid.UUID) -> str:
    return f"GB1:{order_id.hex}"


async def status(db: AsyncSession, order_id: uuid.UUID) -> str:
    found = await db.scalar(
        select(Order.status).where(Order.id == order_id).execution_options(populate_existing=True)
    )
    assert found is not None
    return found


async def tries(db: AsyncSession, order_id: uuid.UUID) -> list[Delivery]:
    rows = await db.execute(
        select(Delivery)
        .where(Delivery.order_id == order_id)
        .order_by(Delivery.attempt)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def stops_of(db: AsyncSession, route_id: uuid.UUID) -> dict[uuid.UUID | None, RouteStop]:
    rows = await db.execute(
        select(RouteStop)
        .where(RouteStop.route_id == route_id)
        .execution_options(populate_existing=True)
    )
    return {s.society_id: s for s in rows.scalars()}


async def started(
    api: Any, google: FakeGoogle, db: AsyncSession, *, leave: tuple[str, ...] = ()
) -> tuple[Day, dict[str, str]]:
    """The day's route started (the orders of `leave` left unpacked); the rider's headers."""
    day = await published_day(db)
    await pack(db, *[o for phone, o in day.orders.items() if phone not in leave])
    rider = await as_user(api, google, ARJUN)
    answer = await api.post(
        f"/v1/routes/{day.route_id}/start",
        json={"leave_behind": bool(leave)},
        headers={**rider, **key()},
    )
    assert answer.status_code == 200, answer.text
    return day, rider


async def skip_all(api: Any, rider: dict[str, str], db: AsyncSession, route_id: uuid.UUID) -> None:
    for stop in (await stops_of(db, route_id)).values():
        if stop.status in ("pending", "arrived"):
            done = await api.post(
                f"/v1/stops/{stop.id}/skip",
                json={"reason": "no_access"},
                headers={**rider, **key()},
            )
            assert done.status_code == 200, done.text


# --- stale locked reads (M4 get, attempts.latest) ---------------------------------------------


async def test_a_locked_order_read_sees_a_move_made_since_it_was_loaded(
    db_session: AsyncSession,
) -> None:
    day = await published_day(db_session)
    order_id = day.orders[PRIYA]
    loaded = await orders_api.get(db_session, order_id)
    # Another transaction refunds the order meanwhile: the object in this session is now stale.
    await db_session.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(status="refunded")
        .execution_options(synchronize_session=False)
    )
    assert loaded.status == "locked"
    with pytest.raises(ProblemError) as stale:
        await orders_api.transition(db_session, order_id, "picking", actor="packer")
    assert stale.value.code == "orders.illegal_transition"
    assert (await orders_api.get(db_session, order_id, for_update=True)).status == "refunded"


async def test_a_locked_attempt_read_sees_a_move_made_since_it_was_loaded(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    with time_machine.travel(MORNING, tick=False):
        day, _ = await started(clients(), google, db_session)
    order_id = day.orders[PRIYA]
    loaded = await attempts.latest(db_session, order_id)
    assert loaded is not None and loaded.status == "on_route"
    await db_session.execute(
        update(Delivery)
        .where(Delivery.order_id == order_id)
        .values(status="refunded")
        .execution_options(synchronize_session=False)
    )
    locked = await attempts.latest(db_session, order_id, for_update=True)
    assert locked is not None and locked is loaded
    assert locked.status == "refunded"  # read again after the lock


@time_machine.travel(MORNING, tick=False)
async def test_a_route_completing_does_not_undo_a_refund_that_committed_meanwhile(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    day, rider = await started(api, google, db_session)
    priya = day.orders[PRIYA]
    await skip_all(api, rider, db_session, day.route_id)  # every bag is still dispatched
    real = execution.route_orders

    async def a_damaged_bag_is_refunded_meanwhile(*args: Any, **kwargs: Any) -> Any:
        found = await real(*args, **kwargs)  # the orders are listed, then the refund commits
        stale = {"synchronize_session": False}
        await db_session.execute(
            update(Order)
            .where(Order.id == priya)
            .values(status="refunded")
            .execution_options(**stale)
        )
        await db_session.execute(
            update(Delivery)
            .where(Delivery.order_id == priya)
            .values(status="refunded")
            .execution_options(**stale)
        )
        return found

    monkeypatch.setattr(execution, "route_orders", a_damaged_bag_is_refunded_meanwhile)
    rider_headers = {**rider, **key()}
    done = await api.post(f"/v1/routes/{day.route_id}/complete", headers=rider_headers)
    monkeypatch.undo()

    assert done.status_code == 200, done.text
    assert await status(db_session, priya) == "refunded"  # not undelivered again
    [attempt] = await tries(db_session, priya)
    assert attempt.status == "refunded"
    others = [o for p, o in day.orders.items() if p != PRIYA]
    assert [await status(db_session, o) for o in others] == ["undelivered"] * 3
    moved = await db_session.scalars(
        select(OrderEvent.to_status).where(OrderEvent.order_id == priya)
    )
    assert "undelivered" not in list(moved)


# --- a concurrent resolve, older attempts, the route's last position, the ping index -----------


@time_machine.travel(MORNING, tick=False)
async def test_two_resolves_at_once_turn_the_unique_violation_into_a_conflict(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, rider = await started(api, google, db_session)
    sunita = day.orders[SUNITA]
    stop = (await stops_of(db_session, day.route_id))[GREEN_VALLEY]
    handed = await api.post(
        f"/v1/stops/{stop.id}/handover",
        json={"crates": 1, "bags": 1},
        headers={**rider, **key()},
    )
    assert handed.status_code == 200, handed.text
    refused = await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    assert refused.json()["status"] == "triage"
    # The other manager's resolve got there first and booked attempt 2.
    [first] = await tries(db_session, sunita)
    db_session.add(
        Delivery(
            order_id=sunita,
            attempt=2,
            hub_id=first.hub_id,
            society_id=first.society_id,
            order_kind=first.order_kind,
            delivery_date=NEXT_DAY,
            slot_id=first.slot_id,
            status="scheduled",
            delivery_code="1234",
        )
    )
    await db_session.commit()
    manager = await as_user(api, google, VIKRAM)
    late = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "redeliver", "note": "me too"},
        headers={**manager, **key()},
    )
    assert (late.status_code, late.json()["code"]) == (409, "logistics.not_undelivered")


async def test_the_evening_job_also_decides_attempts_of_earlier_days(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day, _ = await started(api, google, db_session)
    # 16:00 the next day: `close_routes` completes the route nobody completed, the bags ride back.
    closed = await job(db_session, "close_routes", at(14, 16, 0))
    assert closed["closed"] == 1 and closed["returned_to_hub"] == 4
    assert {a.delivery_date for o in day.orders.values() for a in await tries(db_session, o)} == {
        DAY
    }
    # Nobody ran the 13 October evening job; the 14th's takes those bags up (not left forever).
    summary = await job(db_session, "schedule_redeliveries", at(14, 20, 45))
    assert (summary["redelivery"], summary["failed"]) == (4, 0)
    for order in day.orders.values():
        assert await status(db_session, order) == "redelivery"
        second = (await tries(db_session, order))[1]
        assert second.delivery_date == date(2026, 10, 15)


@time_machine.travel(MORNING, tick=False)
async def test_completing_a_route_clears_its_last_position_and_pings_have_a_route_index(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    day, rider = await started(api, google, db_session)
    await skip_all(api, rider, db_session, day.route_id)
    await valkey.set(
        tracking.loc_key(day.route_id),
        json.dumps({"at": MORNING.isoformat(), "lat": 19.03, "lng": 73.03, "heading": None}),
    )
    done = await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
    assert done.status_code == 200, done.text
    assert await valkey.get(tracking.loc_key(day.route_id)) is None
    index = await db_session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'logistics' AND tablename = 'gps_pings' "
            "AND indexname = 'ix_gps_pings_route_at'"
        )
    )
    assert index is not None and "(route_id, at DESC)" in index


# --- proof photos: claimed, kept 90 days, used once ----------------------------------------------


async def upload(db: AsyncSession, s3: FakeS3, user: uuid.UUID) -> str:
    """What POST /v1/files/presign and the app's PUT leave behind."""
    name = f"delivery_photo/{user}/{uuid.uuid4()}.jpg"
    db.add(
        Upload(
            key=name,
            user_id=user,
            purpose="delivery_photo",
            content_type="image/jpeg",
            size_bytes=9,
        )
    )
    s3.objects[name] = ("image/jpeg", 9)
    await db.commit()
    return name


@time_machine.travel(MORNING, tick=False)
async def test_proof_photos_are_claimed_kept_90_days_after_the_delivery_and_used_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    store: ObjectStore | None = create_store(STORAGE_SETTINGS)
    assert store is not None
    store.client = s3
    api = clients(storage=store)
    day, rider = await started(api, google, db_session)
    captain = await as_user(api, google, MEENA)
    meena, arjun = await user_id(db_session, MEENA), await user_id(db_session, ARJUN)
    stops = await stops_of(db_session, day.route_id)

    # The handover by photo (the rider's phone is dead): the crates photo is claimed and kept.
    crates = await upload(db_session, s3, meena)
    handed = await api.post(
        f"/v1/stops/{stops[SEED_SOCIETY_ID].id}/handover",
        json={"crates": 1, "bags": 2, "photo_key": crates},
        headers={**captain, **key()},
    )
    assert handed.status_code == 200, handed.text
    # A rider in rider mode proves a door by photo.
    green = await api.post(
        f"/v1/stops/{stops[GREEN_VALLEY].id}/handover",
        json={"crates": 1, "bags": 1},
        headers={**rider, **key()},
    )
    assert green.status_code == 200, green.text
    door = await upload(db_session, s3, arjun)
    sunita = day.orders[SUNITA]
    delivered = await api.post(
        f"/v1/deliveries/{sunita}/proof",
        json={"bag_qr": bag(sunita), "proof": "photo", "photo_key": door},
        headers={**rider, **key()},
    )
    assert delivered.status_code == 200, delivered.text
    # The captain reports a refused door with a photo.
    refused_photo = await upload(db_session, s3, meena)
    refused = await api.post(
        f"/v1/deliveries/{day.orders[PRIYA]}/undelivered",
        json={"reason": "refused", "photo_key": refused_photo},
        headers={**captain, **key()},
    )
    assert refused.status_code == 200, refused.text

    kept_until = datetime(2027, 1, 11, tzinfo=UTC) - timedelta(hours=5, minutes=30)  # IST midnight
    for name in (crates, door, refused_photo):
        row = await db_session.get(Upload, name, populate_existing=True)
        assert row is not None
        assert (row.claimed_by, row.delete_after) == ("logistics", kept_until)

    # A photo is proof once: not again for another door, nor for another report.
    for body, path in (
        (
            {"bag_qr": bag(day.orders[MEENA]), "proof": "photo", "photo_key": refused_photo},
            f"/v1/deliveries/{day.orders[MEENA]}/proof",
        ),
        (
            {"reason": "refused", "photo_key": crates},
            f"/v1/deliveries/{day.orders[MEENA]}/undelivered",
        ),
    ):
        again = await api.post(path, json=body, headers={**captain, **key()})
        assert (again.status_code, again.json()["code"]) == (409, "logistics.photo_already_used")
    assert await status(db_session, day.orders[MEENA]) == "at_society"  # nothing moved

    # The photos survive the sweep through their 90 days and go after.
    during = datetime(2026, 12, 20, tzinfo=UTC)
    await run_job(
        db_session,
        "sweep_uploads",
        settings=make_settings(),
        storage=store,
        scheduled_for=during,
    )
    assert {crates, door, refused_photo} <= set(s3.objects)
    after = kept_until + timedelta(minutes=1)
    await run_job(
        db_session, "sweep_uploads", settings=make_settings(), storage=store, scheduled_for=after
    )
    assert not {crates, door, refused_photo} & set(s3.objects)
    held = await db_session.scalars(
        select(Handover.photo_key).where(Handover.photo_key.is_not(None))
    )
    assert list(held) == [crates]  # the row keeps the reference; the object is gone by design


# --- bags in the captain's hands, and the customer watching a redelivery ------------------------


@time_machine.travel(MORNING, tick=False)
async def test_a_bag_the_captain_holds_comes_from_her_next_day_whatever_ended_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, rider = await started(api, google, db_session)
    captain = await as_user(api, google, MEENA)
    stops = await stops_of(db_session, day.route_id)
    handed = await api.post(
        f"/v1/stops/{stops[SEED_SOCIETY_ID].id}/handover",
        json={"crates": 1, "bags": 2, "handover_code": stops[SEED_SOCIETY_ID].handover_code},
        headers={**captain, **key()},
    )
    assert handed.status_code == 200, handed.text
    priya, meena, sunita = day.orders[PRIYA], day.orders[MEENA], day.orders[SUNITA]
    # Priya: the captain reports a refused door. Meena: never scanned, so hold_expiry decides.
    refused = await api.post(
        f"/v1/deliveries/{priya}/undelivered",
        json={"reason": "refused"},
        headers={**captain, **key()},
    )
    assert refused.json()["status"] == "triage"
    # Sunita is in rider mode (no captain): the bag at the society is the rider's.
    green = await api.post(
        f"/v1/stops/{stops[GREEN_VALLEY].id}/handover",
        json={"crates": 1, "bags": 1},
        headers={**rider, **key()},
    )
    assert green.status_code == 200, green.text
    rider_miss = await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    assert rider_miss.json()["status"] == "triage"
    await skip_all(api, rider, db_session, day.route_id)
    done = await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
    assert done.status_code == 200, done.text
    expiry = await job(db_session, "hold_expiry", at(13, 20, 30))
    assert (expiry["not_home"], expiry["not_confirmed"]) == (0, 1)  # Meena's
    assert [a.kept_by_captain for a in await tries(db_session, priya)] == [True]
    assert [a.kept_by_captain for a in await tries(db_session, meena)] == [True]
    assert [a.kept_by_captain for a in await tries(db_session, sunita)] == [False]

    # The hub redelivers the triage ones; attempt 2 keeps the flag.
    with time_machine.travel(at(13, 21, 0), tick=False):
        manager = await as_user(api, google, VIKRAM)
        for order in (priya, meena):
            again = await api.post(
                f"/v1/deliveries/{order}/resolve",
                json={"outcome": "redeliver", "note": "tomorrow"},
                headers={**manager, **key()},
            )
            assert again.status_code == 200, again.text
    assert [a.kept_by_captain for a in (await tries(db_session, priya))] == [True, True]
    assert [a.kept_by_captain for a in (await tries(db_session, meena))] == [True, True]

    # Next morning: the van carries nothing for Sunrise Towers' two bags, yet the stop is there.
    solve = await job(db_session, "solve_routes", at(13, 23, 15))
    assert solve["published"] == 1, solve
    route_id = await db_session.scalar(select(Route.id).where(Route.delivery_date == NEXT_DAY))
    assert route_id is not None
    planned = (await stops_of(db_session, route_id))[SEED_SOCIETY_ID]
    assert planned.crates == 0
    with time_machine.travel(at(14, 6, 30), tick=False):
        rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
        route = await api.post(f"/v1/routes/{route_id}/start", headers={**rider, **key()})
        assert route.status_code == 200, route.text
        sunrise = [s for s in route.json()["stops"] if s["society"]["id"] == str(SEED_SOCIETY_ID)]
        assert [(s["bags"], s["redelivery_bags"]) for s in sunrise] == [(0, 0)]
        # Priya's household may watch the van that brings her kept bag (a redelivery of an earlier
        # day, not one of the route's orders).
        who = SimpleNamespace(user_id=await user_id(db_session, PRIYA))
        permit = Permit(cast(Any, who), "routes:live", (Grant("customer", None),))
        route_row = await db_session.get(Route, route_id, populate_existing=True)
        assert route_row is not None
        seen = await live.audience(db_session, permit, route_row)
        assert seen.customer and seen.stop_id == planned.id
        handover = await api.post(
            f"/v1/stops/{planned.id}/handover",
            json={"crates": 0, "bags": 0, "handover_code": planned.handover_code},
            headers={**captain, **key()},
        )
        assert handover.status_code == 200, handover.text
        assert sorted(handover.json()["at_society"]) == sorted([str(priya), str(meena)])
        assert handover.json()["mismatch"] is False
    assert [await status(db_session, o) for o in (priya, meena)] == ["at_society"] * 2


# --- a left-behind ride is no customer miss ------------------------------------------------------


async def test_a_ride_after_the_hub_left_the_bag_behind_keeps_the_free_redelivery(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day, rider = await started(api, google, db_session, leave=(PRIYA, MEENA))
        await skip_all(api, rider, db_session, day.route_id)
        done = await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
        assert done.status_code == 200, done.text
        await pack(db_session, day.orders[PRIYA], day.orders[MEENA])  # packed after the van left
    assert (await job(db_session, "hold_expiry", at(13, 20, 30)))["left_behind"] == 2
    priya, meena = day.orders[PRIYA], day.orders[MEENA]
    with time_machine.travel(at(13, 21, 0), tick=False):
        manager = await as_user(api, google, VIKRAM)
        for order in (priya, meena):
            ride = await api.post(
                f"/v1/deliveries/{order}/resolve",
                json={"outcome": "redeliver", "note": "tomorrow's van"},
                headers={**manager, **key()},
            )
            assert ride.status_code == 200, ride.text
            assert (ride.json()["attempt"], ride.json()["status"]) == (1, "scheduled")
    # Two Sunrise bags and the day's other returns; the 20:45 job leaves the rides alone.
    await job(db_session, "schedule_redeliveries", at(13, 20, 45))
    assert [(a.attempt, a.status, a.hub_ride) for a in await tries(db_session, meena)] == [
        (1, "scheduled", True)
    ]
    solve = await job(db_session, "solve_routes", at(13, 23, 15))
    assert solve["published"] == 1, solve
    route_id = await db_session.scalar(select(Route.id).where(Route.delivery_date == NEXT_DAY))
    assert route_id is not None

    with time_machine.travel(at(14, 6, 30), tick=False):
        rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
        route = await api.post(f"/v1/routes/{route_id}/start", headers={**rider, **key()})
        assert route.status_code == 200, route.text
        sunrise = (await stops_of(db_session, route_id))[SEED_SOCIETY_ID]
        assert [await status(db_session, o) for o in (priya, meena)] == ["dispatched"] * 2
        handed = await api.post(
            f"/v1/stops/{sunrise.id}/handover",
            json={"crates": 0, "bags": 2, "handover_code": sunrise.handover_code},
            headers={**captain, **key()},
        )
        assert handed.status_code == 200, handed.text
        # Meena is not home (held); Priya's door refuses the bag: both are real misses now.
        held = await api.post(
            f"/v1/deliveries/{meena}/undelivered",
            json={"reason": "not_home"},
            headers={**captain, **key()},
        )
        assert held.json()["status"] == "held"
        refused = await api.post(
            f"/v1/deliveries/{priya}/undelivered",
            json={"reason": "refused"},
            headers={**captain, **key()},
        )
        assert refused.json()["status"] == "triage"
        for stop in (await stops_of(db_session, route_id)).values():
            if stop.status == "pending":
                await api.post(
                    f"/v1/stops/{stop.id}/skip",
                    json={"reason": "no_access"},
                    headers={**rider, **key()},
                )
        closed = await api.post(f"/v1/routes/{route_id}/complete", headers={**rider, **key()})
        assert closed.status_code == 200, closed.text
    # Triage still offers the redelivery for Priya...
    with time_machine.travel(at(14, 21, 0), tick=False):
        manager = await as_user(api, google, VIKRAM)
        again = await api.post(
            f"/v1/deliveries/{priya}/resolve",
            json={"outcome": "redeliver", "note": "one more try"},
            headers={**manager, **key()},
        )
    assert again.status_code == 200, again.text
    assert again.json()["attempt"] == 2
    # ... and the evening job gives Meena's real miss her free redelivery, not a refund.
    await job(db_session, "hold_expiry", at(14, 20, 30))
    summary = await job(db_session, "schedule_redeliveries", at(14, 20, 45))
    assert summary["triage"] == 0
    rows = await tries(db_session, meena)
    assert [(a.attempt, a.status) for a in rows] == [(1, "redelivery"), (2, "scheduled")]
    assert await status(db_session, meena) == "redelivery"


# --- orphan redeliveries -------------------------------------------------------------------------


@time_machine.travel(MORNING, tick=False)
async def test_an_orphan_redelivery_is_rebooked_or_refunded_by_the_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, rider = await started(api, google, db_session)
    manager = await as_user(api, google, VIKRAM)
    sunita = day.orders[SUNITA]
    stop = (await stops_of(db_session, day.route_id))[GREEN_VALLEY]
    await api.post(
        f"/v1/stops/{stop.id}/handover", json={"crates": 1, "bags": 1}, headers={**rider, **key()}
    )
    await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    booked = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "redeliver", "note": "tomorrow"},
        headers={**manager, **key()},
    )
    assert booked.status_code == 200, booked.text

    async def forgotten() -> None:
        """Its day has passed and no route took it."""
        await db_session.execute(
            update(Delivery)
            .where(Delivery.order_id == sunita, Delivery.attempt == 2)
            .values(delivery_date=date(2026, 10, 12), status="scheduled")
        )
        await db_session.commit()

    await forgotten()
    board = await api.get(f"/v1/hubs/{SEED_HUB_ID}/deliveries", headers=manager)
    assert board.json()["orphans"] == [str(sunita)]
    rebooked = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "redeliver", "note": "the next free van"},
        headers={**manager, **key()},
    )
    assert rebooked.status_code == 200, rebooked.text
    assert (rebooked.json()["attempt"], rebooked.json()["delivery_date"]) == (2, "2026-10-14")
    assert (
        rebooked.json()["status"] == "scheduled"
        and await status(db_session, sunita) == "redelivery"
    )
    assert len(await events(db_session, "logistics.redelivery_scheduled")) == 2
    board = await api.get(f"/v1/hubs/{SEED_HUB_ID}/deliveries", headers=manager)
    assert board.json()["orphans"] == []

    await forgotten()
    refunded = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "refund", "note": "no van came"},
        headers={**manager, **key()},
    )
    assert refunded.status_code == 200, refunded.text
    assert (
        refunded.json()["status"] == "refunded" and await status(db_session, sunita) == "refunded"
    )
    [move] = (
        await db_session.execute(
            select(OrderEvent.from_status, OrderEvent.actor, OrderEvent.actor_user_id).where(
                OrderEvent.order_id == sunita, OrderEvent.to_status == "refunded"
            )
        )
    ).all()
    assert move == ("redelivery", "system", await user_id(db_session, VIKRAM))
    assert await orders_api.refundable_paise(db_session, sunita) == 0  # all of it went back
    again = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "refund", "note": "twice"},
        headers={**manager, **key()},
    )
    assert (again.status_code, again.json()["code"]) == (409, "logistics.not_undelivered")


# --- a full wallet at triage ----------------------------------------------------------------------


@time_machine.travel(MORNING, tick=False)
async def test_a_triage_refund_the_wallet_cannot_take_goes_to_the_payment_source(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, rider = await started(api, google, db_session)
    manager = await as_user(api, google, VIKRAM)
    sunita = day.orders[SUNITA]
    stop = (await stops_of(db_session, day.route_id))[GREEN_VALLEY]
    await api.post(
        f"/v1/stops/{stop.id}/handover", json={"crates": 1, "bags": 1}, headers={**rider, **key()}
    )
    await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    owner = await user_id(db_session, SUNITA)
    await paid_topup(db_session, owner, CAP_PAISE - await billing_api.balance(db_session, owner))
    await db_session.commit()
    resolved = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "refund", "note": "refused"},
        headers={**manager, **key()},
    )

    assert resolved.status_code == 200, resolved.text
    assert await status(db_session, sunita) == "refunded"
    assert await billing_api.balance(db_session, owner) == CAP_PAISE  # sent back, not kept
    refund = (
        await db_session.execute(select(Refund).where(Refund.order_id == sunita))
    ).scalar_one()
    assert (refund.reason, refund.status) == ("order", "pending")
    assert refund.amount_paise > 0 and await orders_api.refundable_paise(db_session, sunita) == 0


@time_machine.travel(MORNING, tick=False)
async def test_a_triage_refund_the_wallet_cannot_take_waits_and_a_partial_one_needs_final(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, rider = await started(api, google, db_session)
    manager = await as_user(api, google, VIKRAM)
    sunita = day.orders[SUNITA]
    stop = (await stops_of(db_session, day.route_id))[GREEN_VALLEY]
    await api.post(
        f"/v1/stops/{stop.id}/handover", json={"crates": 1, "bags": 1}, headers={**rider, **key()}
    )
    await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    owner = await user_id(db_session, SUNITA)
    await paid_topup(db_session, owner, CAP_PAISE - 100, age_days=181)  # too old to refund to
    await db_session.commit()
    full = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "refund", "note": "refused"},
        headers={**manager, **key()},
    )
    # The top-ups here are not Razorpay payments, so there is nothing to send the money back to
    # (M5 §3.3): the manager sees the wallet limit and the room, and nothing moved.
    assert (full.status_code, full.json()["code"]) == (422, "billing.wallet_limit")
    assert full.json()["room_paise"] == 100
    assert await status(db_session, sunita) == "undelivered"
    assert [a.status for a in await tries(db_session, sunita)] == ["triage"]
    # What fits is a partial refund: it ends the order, so it is refused unless the manager says
    # it is final (the rest could never go back), and then it does end it.
    part = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "refund", "refund_paise": 100, "note": "what fits"},
        headers={**manager, **key()},
    )
    assert (part.status_code, part.json()["code"]) == (422, "logistics.partial_refund_not_final")
    assert part.json()["max_paise"] > 100 and await status(db_session, sunita) == "undelivered"
    final = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "refund", "refund_paise": 100, "final": True, "note": "what fits"},
        headers={**manager, **key()},
    )
    assert final.status_code == 200, final.text
    assert await status(db_session, sunita) == "refunded"


# --- the customer watching a redelivery; holds of a missed run ------------------------------------


async def test_a_customer_watches_the_van_that_brings_their_redelivery_and_nobody_else(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    savita = "+919800001012"  # Krishna Kunj's captain
    with time_machine.travel(MORNING, tick=False):
        day, rider = await started(api, google, db_session)
        stops = await stops_of(db_session, day.route_id)
        krishna = next(s for s in stops if s not in (SEED_SOCIETY_ID, GREEN_VALLEY))
        for phone, society, bags in ((MEENA, SEED_SOCIETY_ID, 2), (savita, krishna, 1)):
            who = await as_user(api, google, phone)
            handed = await api.post(
                f"/v1/stops/{stops[society].id}/handover",
                json={"crates": 1, "bags": bags, "handover_code": stops[society].handover_code},
                headers={**who, **key()},
            )
            assert handed.status_code == 200, handed.text
        captain = await as_user(api, google, MEENA)
        ramesh = day.orders[RAMESH]
        delivered = await api.post(
            f"/v1/deliveries/{ramesh}/proof",
            json={"bag_qr": bag(ramesh), "proof": "scan"},
            headers={**await as_user(api, google, savita), **key()},
        )
        assert delivered.status_code == 200, delivered.text  # delivered on T, no redelivery
        priya = day.orders[PRIYA]
        refused = await api.post(
            f"/v1/deliveries/{priya}/undelivered",
            json={"reason": "refused"},
            headers={**captain, **key()},
        )
        assert refused.json()["status"] == "triage"
        await skip_all(api, rider, db_session, day.route_id)
        done = await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
        assert done.status_code == 200, done.text
    with time_machine.travel(at(13, 21, 0), tick=False):
        manager = await as_user(api, google, VIKRAM)
        booked = await api.post(
            f"/v1/deliveries/{priya}/resolve",
            json={"outcome": "redeliver", "note": "tomorrow"},
            headers={**manager, **key()},
        )
        assert booked.status_code == 200, booked.text
    assert (await job(db_session, "solve_routes", at(13, 23, 15)))["published"] == 1
    route_id = await db_session.scalar(select(Route.id).where(Route.delivery_date == NEXT_DAY))
    assert route_id is not None
    with time_machine.travel(at(14, 6, 30), tick=False):
        rider = await as_user(api, google, ARJUN)
        started_again = await api.post(f"/v1/routes/{route_id}/start", headers={**rider, **key()})
        assert started_again.status_code == 200, started_again.text
        # Let the stream end at once (a finished route) instead of waiting for the van.
        await db_session.execute(
            update(Route).where(Route.id == route_id).values(status="completed")
        )
        await db_session.commit()
        sse = {"Accept": "text/event-stream"}
        mine = await api.get(
            f"/v1/routes/{route_id}/live", headers={**await as_user(api, google, PRIYA), **sse}
        )
        assert mine.status_code == 200, mine.text
        assert mine.text.startswith("event: snapshot")
        assert "route_completed" in mine.text
        other = await api.get(
            f"/v1/routes/{route_id}/live", headers={**await as_user(api, google, RAMESH), **sse}
        )
        assert (other.status_code, other.json()["code"]) == (403, "auth.forbidden")


async def test_a_hold_whose_20_30_run_was_missed_expires_with_the_next_days_run(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day, _ = await started(api, google, db_session)
        stops = await stops_of(db_session, day.route_id)
        captain = await as_user(api, google, MEENA)
        sunrise = stops[SEED_SOCIETY_ID]
        handed = await api.post(
            f"/v1/stops/{sunrise.id}/handover",
            json={"crates": 1, "bags": 2, "handover_code": sunrise.handover_code},
            headers={**captain, **key()},
        )
        assert handed.status_code == 200, handed.text
        held = await api.post(
            f"/v1/deliveries/{day.orders[MEENA]}/undelivered",
            json={"reason": "not_home"},
            headers={**captain, **key()},
        )
        assert held.json()["status"] == "held"
    # Nobody ran 13 October's 20:30. The 14th's run takes up the held bag and the unscanned one.
    summary = await job(db_session, "hold_expiry", at(14, 20, 30))
    assert (summary["not_home"], summary["not_confirmed"], summary["failed"]) == (1, 1, 0)
    assert await status(db_session, day.orders[MEENA]) == "undelivered"
    assert await status(db_session, day.orders[PRIYA]) == "undelivered"
