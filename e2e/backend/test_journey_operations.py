"""The operations day (MS-B) through the HTTP API, by the real roles: goods receipt and QC, the
packer's queue, a solved route, the rider's start, the captain's handover, the door proofs and the
orders delivered, with what the customers are told.

It runs after `test_simulated_day.py` (conftest orders it) on the database the day migrated, seeded
and simulated. To depend on nothing the day asserted, it uses its own delivery day (a week after
the day's) and its own two households (synthetic phones +919800003xxx, one in each society that has
a captain); it asserts only on their orders, their lots and their messages. The steps build on each
other, in file order, like the day's. Times are the specs' (M3 §6, M4 §6, M6 §6, M7 §6).
"""

import math
import uuid
from asyncio import Runner
from collections.abc import Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import invariants
import pytest
import time_machine
from harness import App, Config, ist, key
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

# Open product bugs this journey found, by the invariant that shows them (xfail(strict=True)).
BUGS: dict[str, str] = {}
VIKRAM = "+919800001008"  # the seed hub's manager
KAVITA = "+919800001007"  # its packer
ARJUN = "+919800001006"  # its rider
MEENA = "+919800001004"  # Sunrise Towers' captain
KRISHNA_KUNJ = uuid.UUID("0199a000-0000-7000-8000-00000000b005")  # the seed's other live society
HUB = f"/v1/hubs/{SEED_HUB_ID}"


@dataclass
class Home:
    """A household of the journey: where it lives, and what its box became."""

    name: str
    phone: str
    society_id: uuid.UUID
    tower: str
    flat: str
    lat: float
    lng: float
    auth: dict[str, str] = field(default_factory=dict[str, str])
    cycle_id: str = ""
    order_id: str = ""
    delivery_code: str = ""

    @property
    def bag_qr(self) -> str:
        return f"GB1:{uuid.UUID(self.order_id).hex}"


@dataclass
class Journey:
    """What the steps learn, for the steps after them."""

    t: date
    homes: dict[str, Home]
    route_id: str = ""
    stops: dict[str, str] = field(default_factory=dict[str, str])  # home name -> stop id
    pos: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])  # sent purchase orders
    lots: list[str] = field(default_factory=list[str])
    packed: int = 0  # tasks packed in the morning (the journey's two and anyone else's)


@pytest.fixture(scope="module")
def trip(config: Config) -> Journey:
    return Journey(
        config.delivery_day + timedelta(days=7),
        {
            "sunrise": Home(
                "sunrise", "+919800003001", SEED_SOCIETY_ID, "c", "C-702", 19.0396, 73.0676
            ),
            "kunj": Home("kunj", "+919800003002", KRISHNA_KUNJ, "a", "A-702", 19.0005, 73.1185),
        },
    )


@contextmanager
def at(when: datetime) -> Iterator[None]:
    with time_machine.travel(when, tick=False):
        yield


def step(loop: Runner, when: datetime, coro: Coroutine[Any, Any, Any]) -> Any:
    with at(when):
        return loop.run(coro)


@asynccontextmanager
async def database(config: Config):
    engine = create_engine(config.database_url, pooled=False)
    try:
        async with AsyncSession(engine) as session:
            yield session
    finally:
        await engine.dispose()


def ok(answer: Any, status: int = 200) -> Any:
    assert answer.status_code == status, (
        f"{answer.request.method} {answer.request.url}: {answer.text}"
    )
    return answer.json() if answer.content else None


async def order_status(app: App, home: Home) -> str:
    return ok(await app.http.get(f"/v1/orders/{home.order_id}", headers=home.auth))["status"]


async def inbox(app: App, home: Home) -> list[str]:
    page = ok(await app.http.get("/v1/notifications", params={"limit": 100}, headers=home.auth))
    return [item["event_type"] for item in page["items"]]


# --- 1. the week before: two new households and their boxes ------------------------------------


def _household(home: Home) -> dict[str, Any]:
    adult = {"age_band": "18_59", "fasting_rules": {}}
    return {
        "society_id": str(home.society_id),
        "address": {
            "tower_id": home.tower,
            "flat": home.flat,
            "location": {"lat": home.lat, "lng": home.lng},
        },
        "diet": "veg",
        "cuisine_tags": ["maharashtrian"],
        "weekday_cook_minutes": 30,
        "weekend_cook_minutes": 60,
        "no_cook_days": [],
        "budget_paise": None,
        "members": [{**adult, "sex": "f"}, {**adult, "sex": "m"}],
        "consents": [
            {"purpose": p, "version": "v1", "granted": True}
            for p in ("service_delivery", "personalisation")
        ],
    }


def test_two_households_join_and_subscribe_and_their_boxes_are_drafted(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Friday T-4 10:00 and Saturday T-3 18:00: `POST /v1/households`, `POST /v1/subscriptions`,
    then M3's weekly job drafts their boxes for T and the box-ready notice opens them."""

    async def join() -> None:
        for home in trip.homes.values():
            home.auth = await app.sign_in(home.phone)
            ok(
                await app.http.post(
                    "/v1/households", json=_household(home), headers={**home.auth, **key()}
                ),
                201,
            )
            ok(
                await app.http.post(
                    "/v1/subscriptions",
                    json={"plan": "autopilot", "payment_mode": "wallet"},
                    headers={**home.auth, **key()},
                ),
                201,
            )

    step(loop, ist(trip.t - timedelta(days=4), 10), join())

    async def draft() -> None:
        when = ist(trip.t - timedelta(days=3), 18)
        assert (await app.job("generate_weekly_plans", when))["delivery_date"] == trip.t.isoformat()
        await app.sweep(when)
        notices = await app.job("send_box_ready_notices", when + timedelta(minutes=30))
        assert notices["delivery_date"] == trip.t.isoformat(), notices
        await app.sweep(when)
        for home in trip.homes.values():
            home.auth = await app.refresh(home.phone)
            box = ok(await app.http.get("/v1/cycles/current", headers=home.auth))
            assert box["delivery_date"] == trip.t.isoformat() and box["lines"], box
            assert box["status"] == "awaiting_approval"
            home.cycle_id = box["id"]

    step(loop, ist(trip.t - timedelta(days=3), 18), draft())


def test_the_households_approve_and_pay_and_the_cut_off_locks_their_orders(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Sunday T-2 10:00: approve (If-Match) and top up the wallet; Monday T-1 21:00: `cutoff_lock`
    debits and locks both orders."""

    async def approve() -> None:
        for home in trip.homes.values():
            home.auth = await app.refresh(home.phone)
            box = ok(await app.http.get(f"/v1/cycles/{home.cycle_id}", headers=home.auth))
            approved = ok(
                await app.http.post(
                    f"/v1/cycles/{home.cycle_id}/approve",
                    headers={**home.auth, **key(), "If-Match": f'"{box["version"]}"'},
                )
            )
            home.order_id = approved["order_id"]
            amount = max(10_000, math.ceil(approved["total_paise"] / 100) * 100)
            started = ok(
                await app.http.post(
                    "/v1/wallet/topups",
                    json={"amount_paise": amount},
                    headers={**home.auth, **key()},
                ),
                201,
            )
            ok(
                await app.http.post(
                    "/v1/dev/razorpay/pay",
                    json={"razorpay_order_id": started["razorpay_order_id"]},
                    headers=home.auth,
                )
            )
        await app.sweep(ist(trip.t - timedelta(days=2), 10))

    step(loop, ist(trip.t - timedelta(days=2), 10), approve())

    async def lock() -> None:
        when = ist(trip.t - timedelta(days=1), 21)
        locked = await app.job("cutoff_lock", when)
        assert locked["locked"] >= 2, locked
        await app.sweep(when)
        for home in trip.homes.values():
            home.auth = await app.refresh(home.phone)
            assert await order_status(app, home) == "locked"

    step(loop, ist(trip.t - timedelta(days=1), 21), lock())


# --- 2. the night: buying, purchase orders and the route ----------------------------------------


def test_the_hub_buys_what_the_locked_orders_need_and_sends_the_purchase_orders(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Monday T-1 21:10 (`build_procurement_plan`) and 22:00: the manager turns the plan into
    purchase orders (`POST /v1/procurement-plans/{id}/purchase-orders`) and sends each."""
    monday = trip.t - timedelta(days=1)

    async def go() -> None:
        assert (await app.job("build_procurement_plan", ist(monday, 21, 10)))["built"] == 1
        vikram = await app.sign_in(VIKRAM)
        plan = ok(
            await app.http.get(
                f"{HUB}/procurement-plans", params={"date": trip.t.isoformat()}, headers=vikram
            )
        )
        assert plan["lines"], plan
        made = ok(
            await app.http.post(
                f"/v1/procurement-plans/{plan['id']}/purchase-orders", headers={**vikram, **key()}
            )
        )["purchase_orders"]
        assert made and {po["status"] for po in made} == {"draft"}, made
        for po in made:
            sent = ok(
                await app.http.post(
                    f"/v1/purchase-orders/{po['id']}/send", headers={**vikram, **key()}
                )
            )
            assert sent["status"] == "sent" and sent["number"], sent
            trip.pos.append(sent)

    step(loop, ist(monday, 22), go())


def test_the_night_solve_publishes_a_route_that_visits_both_societies(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Monday T-1 23:15: `solve_routes` (no ML service: M7's fallback) publishes the box route;
    `GET /v1/hubs/{id}/routes` and `GET /v1/routes/{id}` show a stop for each society."""
    when = ist(trip.t - timedelta(days=1), 23, 15)

    async def go() -> dict[str, Any]:
        assert (await app.job("solve_routes", when))["failed"] == 0
        await app.sweep(when)
        vikram = await app.sign_in(VIKRAM)
        found = ok(
            await app.http.get(
                f"{HUB}/routes", params={"date": trip.t.isoformat(), "kind": "box"}, headers=vikram
            )
        )["items"]
        assert [r["status"] for r in found] == ["published"], found
        return ok(await app.http.get(f"/v1/routes/{found[0]['id']}", headers=vikram))

    route = step(loop, when, go())
    trip.route_id = route["id"]
    for home in trip.homes.values():
        [stop] = [s for s in route["stops"] if s["society"]["id"] == str(home.society_id)]
        assert stop["status"] == "pending" and stop["kind"] == "drop", stop
        trip.stops[home.name] = stop["id"]


# --- 3. the morning at the hub: receiving, QC and packing ----------------------------------------


def test_goods_are_received_with_qc_and_become_lots_with_receive_moves(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Tuesday T 04:00: the packer receives each purchase order
    (`POST /v1/hubs/{id}/goods-receipts`): one kg line short by a kilo (rejected, damaged, grade
    B), the rest in full at grade A. Each accepted line is a lot whose stock is its `receive`
    move."""

    async def go() -> None:
        kavita = await app.sign_in(KAVITA)
        rejected = False
        for po in trip.pos:
            lines: list[dict[str, Any]] = []
            for line in po["lines"]:
                short = not rejected and line["unit"] == "kg" and line["qty"] >= 2
                rejected = rejected or short
                lines.append(
                    {
                        "product_id": line["product_id"],
                        "received_qty": line["qty"],
                        "accepted_qty": round(line["qty"] - 1, 3) if short else line["qty"],
                        "grade": "B" if short else "A",
                        **({"reject_reason": "damaged"} if short else {}),
                    }
                )
            receipt = ok(
                await app.http.post(
                    f"{HUB}/goods-receipts",
                    json={"purchase_order_id": po["id"], "lines": lines},
                    headers={**kavita, **key()},
                ),
                201,
            )
            assert receipt["purchase_order"]["status"] == "received", receipt
            assert all((x["lot_id"] is None) == (x["accepted_qty"] == 0) for x in receipt["lines"])
            for got, sent in zip(receipt["lines"], lines, strict=True):
                assert got["rejected_qty"] == pytest.approx(
                    sent["received_qty"] - sent["accepted_qty"]
                )
                lot = ok(await app.http.get(f"/v1/lots/{got['lot_id']}", headers=kavita))
                assert lot["qty_in"] == lot["qty_on_hand"] == sent["accepted_qty"], lot
                assert [(m["kind"], m["qty"]) for m in lot["moves"]] == [
                    ("receive", sent["accepted_qty"])
                ]
                trip.lots.append(lot["id"])
        assert rejected, "no kg line of two or more to reject"
        stored = ok(
            await app.http.get(
                f"{HUB}/goods-receipts", params={"date": trip.t.isoformat()}, headers=kavita
            )
        )
        assert len(stored["items"]) == len(trip.pos)

    step(loop, ist(trip.t, 4), go())


def test_the_packer_works_the_queue_and_every_bag_is_packed(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Tuesday T 05:00 (`build_packing_tasks`) to 06:00: `POST …/packing-tasks/next` claims the next
    task, each line is picked and the task completed, until the queue answers 204."""

    async def go() -> None:
        await app.job("build_packing_tasks", ist(trip.t, 5))
        kavita = await app.sign_in(KAVITA)
        mine: dict[str, dict[str, Any]] = {}
        while (
            claimed := await app.http.post(f"{HUB}/packing-tasks/next", headers={**kavita, **key()})
        ).status_code == 200:
            task = claimed.json()
            assert task["status"] == "picking" and task["bag_qr"].startswith("GB1:")
            for line in task["lines"]:
                picked = ok(
                    await app.http.post(
                        f"/v1/packing-tasks/{task['id']}/pick",
                        json={"product_id": line["product_id"], "picked_qty": line["qty"]},
                        headers={**kavita, **key()},
                    )
                )
                assert picked["lines_done"] > 0
            done = ok(
                await app.http.post(
                    f"/v1/packing-tasks/{task['id']}/complete", json={}, headers={**kavita, **key()}
                )
            )
            assert done["status"] == "packed" and done["crate_no"], done
            mine[done["order_id"]] = done
            trip.packed += 1
        assert claimed.status_code == 204, claimed.text
        assert {h.order_id for h in trip.homes.values()} <= set(mine), mine
        for home in trip.homes.values():
            home.auth = await app.refresh(home.phone)
            assert await order_status(app, home) == "packed"

    step(loop, ist(trip.t, 5, 30), go())


# --- 4. the van: the rider starts the route ------------------------------------------------------


def test_the_manager_gives_the_route_a_rider_and_the_rider_starts_it(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Tuesday T 06:20 to 06:35: `PATCH /v1/routes/{id}` (rider, If-Match), a stop switched to
    rider mode (`PATCH /v1/stops/{id}`, the absent captain), `POST /v1/routes/{id}/start` moves the
    bags to `dispatched`, a GPS ping is stored."""

    async def go() -> dict[str, Any]:
        vikram, arjun = await app.sign_in(VIKRAM), await app.sign_in(ARJUN)
        rider_id = ok(await app.http.get("/v1/me", headers=arjun))["id"]
        path = f"/v1/routes/{trip.route_id}"
        route = ok(await app.http.get(path, headers=vikram))
        route = ok(
            await app.http.patch(
                path,
                json={"rider_user_id": rider_id},
                headers={**vikram, "If-Match": f'"{route["version"]}"'},
            )
        )
        assert route["rider"]["user_id"] == rider_id, route
        stop = ok(
            await app.http.patch(
                f"/v1/stops/{trip.stops['kunj']}",
                json={"rider_mode": True},
                headers={**vikram, "If-Match": f'"{route["version"]}"'},
            )
        )
        assert stop["rider_mode"] is True
        listed = ok(
            await app.http.get(
                "/v1/riders/me/routes", params={"date": trip.t.isoformat()}, headers=arjun
            )
        )["items"]
        assert [r["id"] for r in listed] == [trip.route_id], listed

        when = ist(trip.t, 6, 30)
        with at(when):
            started = ok(await app.http.post(f"{path}/start", json={}, headers={**arjun, **key()}))
            ping = {"at": when.isoformat(), "lat": 19.0331, "lng": 73.0298, "accuracy_m": 10}
            posted = await app.http.post(
                "/v1/riders/me/location",
                json={"route_id": trip.route_id, "pings": [ping]},
                headers=arjun,
            )
            assert ok(posted, 202)["accepted"] == 1
            assert (await app.job("persist_gps_pings", when + timedelta(minutes=1)))[
                "inserted"
            ] == 1
            await app.sweep(when)
            for home in trip.homes.values():
                home.auth = await app.refresh(home.phone)
                assert await order_status(app, home) == "dispatched"
        return started

    started = step(loop, ist(trip.t, 6, 20), go())
    assert started["status"] == "in_progress" and started["started_at"], started
    sunrise = next(s for s in started["stops"] if s["id"] == trip.stops["sunrise"])
    assert len(sunrise["handover_code"]) == 4 and sunrise["rider_mode"] is False, sunrise
    assert next(s for s in started["stops"] if s["id"] == trip.stops["kunj"])["rider_mode"]


# --- 5. the doors: handover and proof ----------------------------------------------------------


def test_the_captain_takes_the_bags_at_her_stop_with_the_code_and_scans_the_door(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Tuesday T 07:10 to 07:30 at Sunrise Towers: the rider arrives, the captain sees her flats,
    types the handover code (`POST /v1/stops/{id}/handover`, counts as the van carries them), and
    scans the bag at the door (`POST /v1/deliveries/{order_id}/proof`, proof `scan`)."""
    home, stop_id = trip.homes["sunrise"], trip.stops["sunrise"]

    async def go() -> None:
        arjun, meena = await app.sign_in(ARJUN), await app.sign_in(MEENA)
        route = ok(await app.http.get(f"/v1/routes/{trip.route_id}", headers=arjun))
        stop = next(s for s in route["stops"] if s["id"] == stop_id)
        ok(await app.http.post(f"/v1/stops/{stop_id}/arrive", json={}, headers={**arjun, **key()}))

        day = ok(
            await app.http.get(
                "/v1/captains/me/today", params={"date": trip.t.isoformat()}, headers=meena
            )
        )
        flat = next(f for f in day["flats"] if f["order_id"] == home.order_id)
        assert flat["status"] == "on_the_way" and flat["kind"] == "box", flat

        wrong = await app.http.post(
            f"/v1/stops/{stop_id}/handover",
            json={
                "crates": stop["crates"],
                "bags": stop["bags"],
                "handover_code": "0000" if stop["handover_code"] != "0000" else "0001",
            },
            headers={**meena, **key()},
        )
        assert wrong.status_code == 422 and wrong.json()["code"] == "logistics.code_invalid"
        handed = ok(
            await app.http.post(
                f"/v1/stops/{stop_id}/handover",
                json={
                    "crates": stop["crates"],
                    "bags": stop["bags"],
                    "handover_code": stop["handover_code"],
                },
                headers={**meena, **key()},
            )
        )
        assert handed["mismatch"] is False and home.order_id in handed["at_society"], handed
        assert handed["stop"]["status"] == "handed_over"
        home.auth = await app.refresh(home.phone)
        assert await order_status(app, home) == "at_society"

        delivery = ok(
            await app.http.post(
                f"/v1/deliveries/{home.order_id}/proof",
                json={"bag_qr": home.bag_qr, "proof": "scan"},
                headers={**meena, **key()},
            )
        )
        assert (delivery["status"], delivery["proof"], delivery["by"]) == (
            "delivered",
            "scan",
            "captain",
        )
        assert await order_status(app, home) == "delivered"

    step(loop, ist(trip.t, 7, 20), go())


def test_where_the_captain_is_absent_the_rider_hands_over_and_proves_the_door_with_the_otp(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Tuesday T 08:00 at Krishna Kunj (a rider-mode stop): the rider hands over without a code,
    the customer reads the delivery code in her app (`GET /v1/deliveries/{order_id}`), the rider
    proves the door with it (proof `otp`); a rider's `scan` alone is refused."""
    home, stop_id = trip.homes["kunj"], trip.stops["kunj"]

    async def go() -> None:
        arjun = await app.sign_in(ARJUN)
        route = ok(await app.http.get(f"/v1/routes/{trip.route_id}", headers=arjun))
        stop = next(s for s in route["stops"] if s["id"] == stop_id)
        handed = ok(
            await app.http.post(
                f"/v1/stops/{stop_id}/handover",
                json={"crates": stop["crates"], "bags": stop["bags"]},
                headers={**arjun, **key()},
            )
        )
        assert handed["mismatch"] is False and home.order_id in handed["at_society"], handed

        home.auth = await app.refresh(home.phone)
        card = ok(await app.http.get(f"/v1/deliveries/{home.order_id}", headers=home.auth))
        assert card["status"] == "with_captain" and len(card["delivery_code"]) == 4, card
        home.delivery_code = card["delivery_code"]
        scan_only = await app.http.post(
            f"/v1/deliveries/{home.order_id}/proof",
            json={"bag_qr": home.bag_qr, "proof": "scan"},
            headers={**arjun, **key()},
        )
        assert (
            scan_only.status_code == 422 and scan_only.json()["code"] == "logistics.proof_required"
        )
        delivery = ok(
            await app.http.post(
                f"/v1/deliveries/{home.order_id}/proof",
                json={"bag_qr": home.bag_qr, "proof": "otp", "delivery_code": home.delivery_code},
                headers={**arjun, **key()},
            )
        )
        assert (delivery["status"], delivery["proof"], delivery["by"]) == (
            "delivered",
            "otp",
            "rider",
        )
        assert await order_status(app, home) == "delivered"
        done = ok(await app.http.get(f"/v1/deliveries/{home.order_id}", headers=home.auth))
        assert done["proof"]["kind"] == "otp" and done["delivery_code"] is None, done

    step(loop, ist(trip.t, 8), go())


def test_the_rider_completes_the_route(app: App, trip: Journey, loop: Runner) -> None:
    """Tuesday T 09:30: `POST /v1/routes/{id}/complete` with every stop handed over."""

    async def go() -> dict[str, Any]:
        arjun = await app.sign_in(ARJUN)
        return ok(
            await app.http.post(
                f"/v1/routes/{trip.route_id}/complete",
                json={"empties_returned": 0},
                headers={**arjun, **key()},
            )
        )

    route = step(loop, ist(trip.t, 9, 30), go())
    assert route["status"] == "completed" and route["completed_at"], route
    assert route["stops_done"] == route["stops_total"], route


# --- 6. after the doors: the stock, the messages and the invariants -----------------------------


def test_the_noon_run_draws_the_packed_bags_down_from_the_received_lots(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Tuesday T 12:30: `surplus_cascade` draws the packed tasks down, first expiry first out, as
    `pack` moves; every lot received in the morning still holds its moves' sum."""
    when = ist(trip.t, 12, 30)

    async def go() -> list[dict[str, Any]]:
        summary = await app.job("surplus_cascade", when)
        assert summary["drawn_tasks"] >= trip.packed >= 2, summary
        await app.sweep(when)
        kavita = await app.sign_in(KAVITA)
        return [ok(await app.http.get(f"/v1/lots/{lot}", headers=kavita)) for lot in trip.lots]

    lots = step(loop, when, go())
    for lot in lots:
        moves = sum(m["qty"] for m in lot["moves"])
        assert lot["qty_on_hand"] == pytest.approx(moves), lot
        assert lot["moves"][0]["kind"] == "receive", lot
    assert any(m["kind"] == "pack" and m["qty"] < 0 for lot in lots for m in lot["moves"]), lots


def test_the_customers_were_told_at_each_step(app: App, trip: Journey, loop: Runner) -> None:
    """The inbox (`GET /v1/notifications`) holds one message each for dispatched, at society and
    delivered, in that order of events, and a push went to the household's phone for each."""

    async def go() -> None:
        when = ist(trip.t, 12, 40)
        await app.sweep(when)
        await app.job("send_deliveries", when)
        for home in trip.homes.values():
            home.auth = await app.refresh(home.phone)
            kinds = await inbox(app, home)
            for kind in ("orders.dispatched", "orders.at_society", "orders.delivered"):
                assert kinds.count(kind) == 1, (home.name, kind, kinds)
            assert kinds.index("orders.delivered") < kinds.index("orders.at_society")
            assert kinds.index("orders.at_society") < kinds.index("orders.dispatched")
            pushed = [p for p in app.outside.pushes if p["token"] == app.devices[home.phone]]
            assert len(pushed) >= 3, (home.name, len(pushed))

    step(loop, ist(trip.t, 12, 40), go())


def test_the_evening_jobs_find_nothing_left_to_do(app: App, trip: Journey, loop: Runner) -> None:
    """Tuesday T 16:00 to 20:45: the route is closed and every bag delivered, so `close_routes`,
    `hold_expiry` and `schedule_redeliveries` change nothing for the journey's orders."""

    async def go() -> None:
        for name, hour, minute in (
            ("close_routes", 16, 0),
            ("hold_expiry", 20, 30),
            ("schedule_redeliveries", 20, 45),
        ):
            await app.job(name, ist(trip.t, hour, minute))
        await app.sweep(ist(trip.t, 20, 45))
        for home in trip.homes.values():
            home.auth = await app.refresh(home.phone)
            assert await order_status(app, home) == "delivered"

    step(loop, ist(trip.t, 16), go())


# --- 7. invariants ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=BUGS[name]))
        if name in BUGS
        else name
        for name in invariants.ALL
    ],
)
def test_invariant(name: str, config: Config, loop: Runner, app: App) -> None:
    """The ledger, stock-equals-moves, the state machines and the outbox still hold after the
    journey (the same checks as the day's, now over its orders too)."""

    async def check() -> list[str] | None:
        async with database(config) as session:
            return await invariants.ALL[name](session)

    violations = loop.run(check())
    if violations is None:
        pytest.skip(f"{name}: its tables are not built yet")
    assert violations == [], "\n".join(violations[:50])
