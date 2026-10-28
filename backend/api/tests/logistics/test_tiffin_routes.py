"""A tiffin route on the day (M7 §3.7): the rider starts it (nothing moves), collects each
kitchen's ready tiffins by its GB1A: code (packed → dispatched as the rider), hands a captain her
society's tiffins by count, and the doors are proved with the short code and the household's code;
a tiffin that does not reach its door is refunded at once, one never collected waits for the hub,
and at 16:00 whatever is still out goes to triage."""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import live
from gharbasket.modules.logistics.models import Delivery, Route, RouteStop, StopOrder
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.modules.partners.models import AssignmentOrder
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import ARJUN, MEENA, PRIYA, RAMESH, SUNITA, VIKRAM, as_user, key, user_id, vehicle
from .tiffin_day import (
    ASHA,
    CLOSE_AT,
    KRISHNA,
    LATA,
    SOLVE_AT,
    START_AT,
    SUNRISE,
    assign,
    kitchen,
    lunch_slot,
    qr,
    ready,
    tiffin,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")  # no captain: rider mode
KRISHNA_KUNJ = uuid.UUID("0199a000-0000-7000-8000-00000000b005")


@dataclass(frozen=True)
class Lunch:
    route_id: uuid.UUID
    lata: uuid.UUID  # Lata's assignment: Priya (Sunrise, 2) and Sunita (Green Valley, 1)
    asha: uuid.UUID  # Asha's: Meena (Sunrise, 1) and Ramesh (Krishna Kunj, 1)
    kitchens: dict[uuid.UUID, uuid.UUID]  # assignment -> kitchen stop
    drops: dict[uuid.UUID, uuid.UUID]  # society -> drop stop
    orders: dict[str, uuid.UUID]  # phone -> tiffin


async def lunch_route(db: AsyncSession) -> Lunch:
    """Two kitchens, four tiffins and Arjun's two-wheeler, solved and published at 09:35."""
    slot = await lunch_slot(db)
    lata = await kitchen(db, LATA, "Lata tai", at=SUNRISE)
    asha = await kitchen(db, ASHA, "Asha tai", at=KRISHNA)
    orders = {
        PRIYA: await tiffin(db, PRIYA, slot, 2),
        SUNITA: await tiffin(db, SUNITA, slot, 1),
        MEENA: await tiffin(db, MEENA, slot, 1),
        RAMESH: await tiffin(db, RAMESH, slot, 1),
    }
    at_lata = await assign(db, lata, slot, {orders[PRIYA]: 2, orders[SUNITA]: 1})
    at_asha = await assign(db, asha, slot, {orders[MEENA]: 1, orders[RAMESH]: 1})
    van = await vehicle(db, "TW-1", kg=40, crates=0, kind="two_wheeler")
    van.default_rider_user_id = await user_id(db, ARJUN)
    await db.commit()
    run = await run_job(
        db, "solve_tiffin_routes", settings=make_settings(), scheduled_for=SOLVE_AT, http=None
    )
    assert run.summary is not None and run.summary["published"] == 1, run.summary
    route = await db.scalar(select(Route).where(Route.kind == "tiffin"))
    assert route is not None
    stops = list(
        (await db.execute(select(RouteStop).where(RouteStop.route_id == route.id))).scalars()
    )
    return Lunch(
        route.id,
        at_lata,
        at_asha,
        {s.assignment_id: s.id for s in stops if s.assignment_id is not None},
        {s.society_id: s.id for s in stops if s.society_id is not None},
        orders,
    )


async def statuses(db: AsyncSession, *order_ids: uuid.UUID) -> list[str]:
    rows = await db.execute(
        select(Order.id, Order.status)
        .where(Order.id.in_(order_ids))
        .execution_options(populate_existing=True)
    )
    found = dict(rows.all())
    return [found[o] for o in order_ids]


async def attempt(db: AsyncSession, order_id: uuid.UUID) -> Delivery:
    found = await db.scalar(
        select(Delivery)
        .where(Delivery.order_id == order_id)
        .execution_options(populate_existing=True)
    )
    assert found is not None
    return found


async def stop(db: AsyncSession, stop_id: uuid.UUID) -> RouteStop:
    found = await db.get(RouteStop, stop_id, populate_existing=True)
    assert found is not None
    return found


async def start(api: Any, rider: dict[str, str], lunch: Lunch) -> dict[str, Any]:
    answer = await api.post(f"/v1/routes/{lunch.route_id}/start", headers={**rider, **key()})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def collect(api: Any, rider: dict[str, str], stop_id: uuid.UUID, code: str) -> Any:
    return await api.post(
        f"/v1/stops/{stop_id}/collect", json={"pickup_qr": code}, headers={**rider, **key()}
    )


def kitchen_view(route: dict[str, Any], stop_id: uuid.UUID) -> dict[str, Any]:
    [found] = [s for s in route["stops"] if s["id"] == str(stop_id)]
    return found


@time_machine.travel(START_AT, tick=False)
async def test_start_moves_nothing_needs_a_rider_and_shows_the_kitchen_at_pickup_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    lunch = await lunch_route(db_session)
    api = clients()
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    path = f"/v1/routes/{lunch.route_id}"
    lata_stop = lunch.kitchens[lunch.lata]
    before = (await api.get(path, headers=rider)).json()
    shown = kitchen_view(before, lata_stop)
    assert shown["kind"] == "kitchen" and shown["society"] is None
    assert (shown["kitchen"]["display_name"], shown["kitchen"]["ready_by"]) == ("Lata tai", "11:30")
    assert (shown["kitchen"]["address"], shown["kitchen"]["location"]) == (None, None)
    assert {t["status"] for t in shown["tiffins"]} == {"to_collect"}
    assert shown["portions"] == 3
    staff = kitchen_view((await api.get(path, headers=manager)).json(), lata_stop)
    assert staff["kitchen"]["location"] is not None

    await db_session.execute(
        update(Route).where(Route.id == lunch.route_id).values(rider_user_id=None)
    )
    await db_session.commit()
    riderless = await api.post(f"{path}/start", headers={**manager, **key()})
    assert (riderless.status_code, riderless.json()["code"]) == (409, "logistics.rider_required")
    await db_session.execute(
        update(Route)
        .where(Route.id == lunch.route_id)
        .values(rider_user_id=await user_id(db_session, ARJUN))
    )
    await db_session.commit()

    started = await start(api, rider, lunch)

    assert started["status"] == "in_progress"
    assert await statuses(db_session, *lunch.orders.values()) == ["locked"] * 4
    at_lata = kitchen_view(started, lata_stop)
    assert at_lata["kitchen"]["location"] is not None and at_lata["navigate_url"]
    drops = {s["society"]["id"]: s for s in started["stops"] if s["kind"] == "drop"}
    assert drops[str(SEED_SOCIETY_ID)]["handover_code"] is not None
    assert (
        drops[str(GREEN_VALLEY)]["rider_mode"] and drops[str(GREEN_VALLEY)]["handover_code"] is None
    )
    assert (await db_session.scalar(select(Delivery.id).limit(1))) is None


@time_machine.travel(START_AT, tick=False)
async def test_collect_by_the_kitchens_code_dispatches_its_ready_tiffins_as_the_rider(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    lunch = await lunch_route(db_session)
    api = clients()
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    lata_stop, sunrise = lunch.kitchens[lunch.lata], lunch.drops[SEED_SOCIETY_ID]
    early = await collect(api, rider, lata_stop, qr(lunch.lata))
    assert (early.status_code, early.json()["code"]) == (409, "logistics.route_not_started")
    await start(api, rider, lunch)
    refusals = [
        (await collect(api, manager, lata_stop, qr(lunch.lata)), 403, "auth.forbidden"),
        (await collect(api, rider, sunrise, qr(lunch.lata)), 422, "logistics.not_a_kitchen_stop"),
        (await collect(api, rider, lata_stop, "GB1A:nothex"), 422, "logistics.pickup_code_invalid"),
        (
            await collect(api, rider, lata_stop, qr(uuid.uuid4())),
            422,
            "logistics.pickup_code_invalid",
        ),
        (
            await collect(api, rider, lata_stop, qr(lunch.asha)),
            422,
            "logistics.pickup_wrong_kitchen",
        ),
    ]
    for answer, code, problem in refusals:
        assert (answer.status_code, answer.json()["code"]) == (code, problem), answer.text
    nothing = await collect(api, rider, lata_stop, qr(lunch.lata))
    assert (nothing.status_code, nothing.json()["code"]) == (409, "logistics.nothing_ready")
    assert {w["order_id"] for w in nothing.json()["not_ready"]} == {
        str(lunch.orders[PRIYA]),
        str(lunch.orders[SUNITA]),
    }

    await ready(db_session, lunch.lata, LATA, [lunch.orders[PRIYA]])
    first = await collect(api, rider, lata_stop, qr(lunch.lata))

    assert first.status_code == 200, first.text
    out = first.json()
    assert [c["order_id"] for c in out["collected"]] == [str(lunch.orders[PRIYA])]
    sunrise_seq = (await stop(db_session, sunrise)).seq
    assert out["collected"][0]["drop_seq"] == sunrise_seq
    assert [w["order_id"] for w in out["not_ready"]] == [str(lunch.orders[SUNITA])]
    assert (out["already"], out["not_on_route"]) == ([], [])
    assert (out["stop"]["status"], out["stop"]["bags"]) == ("handed_over", 1)
    assert out["stop"]["kitchen"]["address"] is None  # collected: hidden from the rider again
    assert await statuses(db_session, lunch.orders[PRIYA]) == ["dispatched"]
    dispatched = await db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == lunch.orders[PRIYA], OrderEvent.to_status == "dispatched"
        )
    )
    assert dispatched is not None and dispatched.actor == "rider"
    opened = await attempt(db_session, lunch.orders[PRIYA])
    assert (opened.status, opened.stop_id, opened.short_code) == (
        "on_route",
        sunrise,
        (
            await db_session.scalar(
                select(AssignmentOrder.short_code).where(
                    AssignmentOrder.order_id == lunch.orders[PRIYA]
                )
            )
        ),
    )

    # A tiffin added to her assignment after the solve is on no drop of this route.
    late = await tiffin(db_session, PRIYA, opened.slot_id, 1)
    db_session.add(
        AssignmentOrder(assignment_id=lunch.lata, order_id=late, portions=1, short_code="T-99")
    )
    await db_session.commit()
    await ready(db_session, lunch.lata, LATA)  # Sunita's and the late one; M8 not relayed yet
    second = (await collect(api, rider, lata_stop, qr(lunch.lata))).json()
    assert [c["order_id"] for c in second["collected"]] == [str(lunch.orders[SUNITA])]
    assert second["already"] == [str(lunch.orders[PRIYA])]
    assert second["not_on_route"] == [str(late)]
    assert await statuses(db_session, late) == ["packed"]  # left at the kitchen


@time_machine.travel(START_AT, tick=False)
async def test_a_captain_takes_her_tiffins_by_count_and_proves_each_door(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    lunch = await lunch_route(db_session)
    api = clients()
    rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
    await start(api, rider, lunch)
    await ready(db_session, lunch.lata, LATA)
    await ready(db_session, lunch.asha, ASHA)
    for assignment in (lunch.lata, lunch.asha):
        assert (await collect(api, rider, lunch.kitchens[assignment], qr(assignment))).is_success
    sunrise = await stop(db_session, lunch.drops[SEED_SOCIETY_ID])
    path, code = f"/v1/stops/{sunrise.id}/handover", sunrise.handover_code
    priya, meena = (
        await attempt(db_session, lunch.orders[PRIYA]),
        await attempt(db_session, lunch.orders[MEENA]),
    )

    short = await api.post(
        path, json={"crates": 0, "bags": 1, "handover_code": code}, headers={**captain, **key()}
    )
    scanned = await api.post(
        path,
        json={
            "crates": 0,
            "bags": 1,
            "handover_code": code,
            "scanned_bags": [f"GB1:{lunch.orders[PRIYA].hex}"],
        },
        headers={**captain, **key()},
    )
    taken = await api.post(
        path, json={"crates": 0, "bags": 2, "handover_code": code}, headers={**captain, **key()}
    )
    assert (short.status_code, short.json()["code"]) == (409, "logistics.count_mismatch")
    assert short.json()["expected"] == {"crates": 0, "bags": 2}
    assert set(short.json()["short_codes"]) == {priya.short_code, meena.short_code}
    assert (scanned.status_code, scanned.json()["code"]) == (422, "request.invalid")
    assert taken.status_code == 200, taken.text
    assert (taken.json()["at_society"], set(taken.json()["with_captain"])) == (
        [],
        {str(lunch.orders[PRIYA]), str(lunch.orders[MEENA])},
    )
    assert (
        await statuses(db_session, lunch.orders[PRIYA], lunch.orders[MEENA]) == ["dispatched"] * 2
    )
    assert (await attempt(db_session, lunch.orders[PRIYA])).status == "with_captain"
    at_society = await db_session.scalar(
        select(OutboxEvent.id).where(OutboxEvent.event_type == "orders.at_society").limit(1)
    )
    assert at_society is None

    door = f"/v1/deliveries/{lunch.orders[PRIYA]}/proof"
    otp = {"short_code": priya.short_code, "proof": "otp", "delivery_code": priya.delivery_code}
    scan = await api.post(
        door, json={**otp, "proof": "scan", "delivery_code": None}, headers={**captain, **key()}
    )
    other = await api.post(door, json={**otp, "short_code": "T-999"}, headers={**captain, **key()})
    both = await api.post(
        door,
        json={**otp, "bag_qr": f"GB1:{lunch.orders[PRIYA].hex}"},
        headers={**captain, **key()},
    )
    done = await api.post(door, json=otp, headers={**captain, **key()})
    not_home = await api.post(
        f"/v1/deliveries/{lunch.orders[MEENA]}/undelivered",
        json={"reason": "not_home"},
        headers={**captain, **key()},
    )
    assert (scan.status_code, scan.json()["code"]) == (422, "logistics.proof_required")
    assert (other.status_code, other.json()["code"]) == (422, "logistics.bag_mismatch")
    assert (both.status_code, both.json()["code"]) == (422, "request.invalid")
    assert done.status_code == 200, done.text
    assert (done.json()["status"], done.json()["by"]) == ("delivered", "captain")
    assert not_home.status_code == 200, not_home.text
    assert not_home.json()["status"] == "refunded"  # never held: refunded at once, in full
    assert await statuses(db_session, lunch.orders[PRIYA], lunch.orders[MEENA]) == [
        "delivered",
        "refunded",
    ]
    assert (await attempt(db_session, lunch.orders[MEENA])).refund_paise == 6_000

    day = (await api.get("/v1/captains/me/today", headers=captain)).json()
    tiffins = {f["order_id"]: f for f in day["flats"] if f["kind"] == "tiffin"}
    assert set(tiffins) == {str(lunch.orders[PRIYA]), str(lunch.orders[MEENA])}
    assert tiffins[str(lunch.orders[PRIYA])]["short_code"] == priya.short_code
    assert tiffins[str(lunch.orders[PRIYA])]["status"] == "delivered"
    assert tiffins[str(lunch.orders[MEENA])]["status"] == "undelivered"
    [mine] = [s for s in day["stops"] if s["stop_id"] == str(sunrise.id)]
    assert mine["expected"] == {"crates": 0, "bags": 2, "redelivery_bags": 0}


@time_machine.travel(START_AT, tick=False)
async def test_the_rider_delivers_a_rider_mode_drop_and_the_route_ends_with_refunds_and_triage(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    lunch = await lunch_route(db_session)
    api = clients()
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    customer = await as_user(api, google, SUNITA)
    await start(api, rider, lunch)
    await ready(db_session, lunch.lata, LATA)
    card = (await api.get(f"/v1/deliveries/{lunch.orders[SUNITA]}", headers=customer)).json()
    assert (card["status"], card["tracking"], card["captain"]) == ("scheduled", None, None)
    assert card["eta"] is not None and card["delivery_code"] is None
    watch = f"/v1/routes/{lunch.route_id}/live"
    sse = {"Accept": "text/event-stream"}
    assert (await api.get(watch, headers={**customer, **sse})).status_code == 403  # not collected
    assert (await collect(api, rider, lunch.kitchens[lunch.lata], qr(lunch.lata))).is_success
    card = (await api.get(f"/v1/deliveries/{lunch.orders[SUNITA]}", headers=customer)).json()
    assert card["status"] == "on_route" and card["tracking"] == {"route_id": str(lunch.route_id)}
    assert card["delivery_code"] is not None

    green = lunch.drops[GREEN_VALLEY]
    sunita = await attempt(db_session, lunch.orders[SUNITA])

    async def delivered(_: float) -> None:  # the stream ends once her tiffin is not on the way
        await db_session.execute(
            update(Delivery).where(Delivery.id == sunita.id).values(status="delivered")
        )
        await db_session.commit()

    body = live.stream(
        db_session,
        None,
        lunch.route_id,
        live.Audience(green, customer=True, tiffin_id=lunch.orders[SUNITA]),
        sleep=delivered,
        clock=lambda: 0.0,
    )
    frames = b"".join([chunk async for chunk in body]).decode()
    assert frames.startswith("event: snapshot") and '"reason":"handed_over"' in frames
    await db_session.execute(
        update(Delivery).where(Delivery.id == sunita.id).values(status="on_route")
    )
    await db_session.commit()
    route = (await api.get(f"/v1/routes/{lunch.route_id}", headers=rider)).json()
    [tiffin_there] = kitchen_view(route, green)["tiffins"]
    assert tiffin_there["flat_label"] and tiffin_there["status"] == "on_route"
    sunita = await attempt(db_session, lunch.orders[SUNITA])
    listed = await api.get(f"/v1/stops/{green}/deliveries", headers=rider)
    done = await api.post(
        f"/v1/deliveries/{lunch.orders[SUNITA]}/proof",
        json={
            "short_code": sunita.short_code,
            "proof": "otp",
            "delivery_code": sunita.delivery_code,
        },
        headers={**rider, **key()},
    )
    assert [f["short_code"] for f in listed.json()["items"]] == [sunita.short_code]
    assert (done.status_code, done.json()["by"]) == (200, "rider")
    assert (await stop(db_session, green)).status == "handed_over"  # its last tiffin

    # Priya's tiffin rides on; Asha never readied: her stop is skipped, Sunrise too.
    route_path = f"/v1/routes/{lunch.route_id}"
    for skipped in (lunch.kitchens[lunch.asha], lunch.drops[SEED_SOCIETY_ID]):
        answer = await api.post(
            f"/v1/stops/{skipped}/skip", json={"reason": "other"}, headers={**rider, **key()}
        )
        assert answer.status_code == 200, answer.text
    krishna = await api.post(
        f"/v1/stops/{lunch.drops[KRISHNA_KUNJ]}/skip",
        json={"reason": "other"},
        headers={**rider, **key()},
    )
    assert krishna.status_code == 200
    completed = await api.post(f"{route_path}/complete", headers={**rider, **key()})
    assert completed.status_code == 200, completed.text
    priya = await attempt(db_session, lunch.orders[PRIYA])
    assert (priya.status, priya.reason, priya.refund_paise) == (
        "refunded",
        "returned_to_hub",
        12_000,
    )
    assert await statuses(db_session, lunch.orders[PRIYA]) == ["refunded"]
    for phone in (MEENA, RAMESH):  # Asha's, never collected: the hub's
        left = await attempt(db_session, lunch.orders[phone])
        assert (left.status, left.reason, left.short_code is not None) == (
            "triage",
            "left_behind",
            True,
        )

    resolve = f"/v1/deliveries/{lunch.orders[RAMESH]}/resolve"
    again = await api.post(
        resolve, json={"outcome": "redeliver", "note": "next day"}, headers={**manager, **key()}
    )
    assert (again.status_code, again.json()["code"]) == (409, "logistics.no_redelivery")
    refunded = await api.post(
        resolve, json={"outcome": "refund", "note": "kitchen late"}, headers={**manager, **key()}
    )
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["status"] == "refunded"
    assert await statuses(db_session, lunch.orders[RAMESH]) == ["refunded"]
    moves = await db_session.execute(
        select(OrderEvent.to_status, OrderEvent.actor)
        .where(OrderEvent.order_id == lunch.orders[RAMESH])
        .order_by(OrderEvent.id)
    )
    assert [tuple(m) for m in moves.all()][-5:] == [
        ("picking", "hub"),
        ("packed", "hub"),
        ("dispatched", "hub"),
        ("undelivered", "hub"),
        ("refunded", "system"),
    ]


@time_machine.travel(START_AT, tick=False)
async def test_close_routes_sends_tiffins_still_out_to_triage_and_moves_none(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    lunch = await lunch_route(db_session)
    api = clients()
    rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
    await start(api, rider, lunch)
    await ready(db_session, lunch.lata, LATA)
    assert (await collect(api, rider, lunch.kitchens[lunch.lata], qr(lunch.lata))).is_success
    sunrise = await stop(db_session, lunch.drops[SEED_SOCIETY_ID])
    taken = await api.post(
        f"/v1/stops/{sunrise.id}/handover",
        json={"crates": 0, "bags": 1, "handover_code": sunrise.handover_code},
        headers={**captain, **key()},
    )
    assert taken.status_code == 200, taken.text
    while (await relay(db_session)).sent:  # M8 marks the collected tiffins picked up
        pass
    await db_session.commit()

    run = await run_job(
        db_session, "close_routes", settings=make_settings(), scheduled_for=CLOSE_AT
    )

    assert run.summary is not None, run
    assert (run.summary["closed"], run.summary["tiffins_to_triage"]) == (1, 2)
    for phone in (PRIYA, SUNITA):  # with the captain, with the rider: neither moved
        out = await attempt(db_session, lunch.orders[phone])
        assert (out.status, out.reason, out.by_actor) == ("triage", "not_confirmed", "system")
    assert (
        await statuses(db_session, lunch.orders[PRIYA], lunch.orders[SUNITA]) == ["dispatched"] * 2
    )
    for phone in (MEENA, RAMESH):
        assert (await attempt(db_session, lunch.orders[phone])).reason == "left_behind"
    route = await db_session.get(Route, lunch.route_id, populate_existing=True)
    assert route is not None and route.status == "completed"
    assert await db_session.scalar(select(StopOrder.id).limit(1)) is not None
