"""Return-leg pickups (M7 §3.6): route start expects each society's open empties, the donations M9
scheduled for it and the bags of boxes refunded on their second attempt in the captain's hands;
the route's rider reports them in parts; `logistics.pickup_completed` tells M9 (donations) and M6
(returned bags); what is still expected at completion was not ready; the hub's list."""

import uuid
from datetime import date, time
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Donation
from gharbasket.modules.hub_ops import jobs as hub_jobs
from gharbasket.modules.hub_ops.models import KeptItem
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.logistics import deliveries, pickups, solver
from gharbasket.modules.logistics.models import Delivery, FieldEvent, Pickup, RouteStop
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, seed_delivery

from ..hub_ops.test_kept_items import missed_bag
from ..hub_ops.test_packing import NOW as PACKING_MORNING
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    DAY,
    MEENA,
    MORNING,
    NEHA,
    PRIYA,
    RAMESH,
    SANJAY,
    VIKRAM,
    Day,
    as_user,
    key,
    locked_order,
    pack,
    published_day,
    slot,
    user_id,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

KRISHNA_KUNJ = uuid.UUID("0199a000-0000-7000-8000-00000000b005")
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")
OTHER_HUB = uuid.UUID("0199a000-0000-7000-8000-00000000a0ff")
YESTERDAY, NEXT_TUESDAY = date(2026, 10, 12), date(2026, 10, 20)
HUB = f"/v1/hubs/{SEED_HUB_ID}/pickups"


async def donation(db: AsyncSession, phone: str, items: list[dict[str, Any]]) -> uuid.UUID:
    """A donation scheduled for DAY at the household's society (M9 §3.4); committed."""
    owner = await user_id(db, phone)
    household = await identity_api.household_for_user(db, owner)
    assert household is not None and household.society_id is not None
    row = Donation(
        user_id=owner,
        household_id=household.id,
        hub_id=SEED_HUB_ID,
        society_id=household.society_id,
        items=items,
        pickup_on=DAY,
        estimated_points=100,
    )
    db.add(row)
    await db.commit()
    return row.id


async def held_bag(db: AsyncSession, phone: str) -> uuid.UUID:
    """Yesterday's box of `phone`, refunded on its second attempt while the captain held it."""
    order_id = await locked_order(db, phone, await slot(db, time(7)), YESTERDAY)
    for to in ("packed", "dispatched", "at_society", "undelivered"):
        await orders_api.transition(db, order_id, to, actor="hub")
    order = await orders_api.get(db, order_id)
    amount, _ = await deliveries.second_miss_refund(db, order)
    await orders_api.transition(
        db,
        order_id,
        "refunded",
        actor="system",
        payload={"refund_paise": amount, "reason": "second_miss"},
    )
    for attempt, status in ((1, "redelivery"), (2, "refunded")):
        db.add(
            Delivery(
                order_id=order_id,
                attempt=attempt,
                hub_id=SEED_HUB_ID,
                society_id=order.society_id,
                order_kind="box",
                delivery_date=YESTERDAY,
                slot_id=order.slot_id,
                status=status,
                reason="not_home",
                kept_by_captain=True,
                delivery_code="0000",
            )
        )
    await db.commit()
    return order_id


async def stop_of(db: AsyncSession, day: Day, society_id: uuid.UUID) -> RouteStop:
    found = await db.scalar(
        select(RouteStop)
        .where(RouteStop.route_id == day.route_id, RouteStop.society_id == society_id)
        .execution_options(populate_existing=True)
    )
    assert found is not None
    return found


async def started(api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession) -> Day:
    day = await published_day(db)
    await pack(db, *day.orders.values())
    rider = await as_user(api, google, ARJUN)
    begun = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert begun.status_code == 200, begun.text
    return day


async def events(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == "logistics.pickup_completed")
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def report(
    api: httpx2.AsyncClient,
    auth: dict[str, str],
    stop_id: uuid.UUID,
    body: dict[str, Any],
    idem: dict[str, str] | None = None,
) -> httpx2.Response:
    return await api.post(
        f"/v1/stops/{stop_id}/pickups", json=body, headers={**auth, **(idem or key())}
    )


async def drain(db: AsyncSession) -> None:
    while (await relay(db)).sent:
        pass


async def test_the_rider_takes_back_donations_and_a_held_bag_and_m9_records_them(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        await seed_delivery(db_session)  # Tuesdays, so a not-ready donation rolls a week
        await db_session.commit()
        clothes = await donation(db_session, PRIYA, [{"code": "clothes", "unit": "kg", "qty": 3}])
        toys = await donation(db_session, MEENA, [{"code": "toys", "unit": "each", "qty": 2}])
        bag = await held_bag(db_session, RAMESH)
        day = await started(api, google, db_session)
        rider = await as_user(api, google, ARJUN)
        sunrise = (await stop_of(db_session, day, SEED_SOCIETY_ID)).id
        kunj = (await stop_of(db_session, day, KRISHNA_KUNJ)).id
        green = (await stop_of(db_session, day, GREEN_VALLEY)).id

        # Route start expected them: the donations on Sunrise's stop (no flats at a captain's
        # stop), the bag on Krishna Kunj's, and an empties row on every stop.
        detail = (await api.get(f"/v1/routes/{day.route_id}", headers=rider)).json()
        views = {s["id"]: s for s in detail["stops"]}
        assert views[str(sunrise)]["pickups"] == {
            "empties_collected": None,
            "donations": [
                {
                    "donation_id": str(clothes),
                    "items": "clothes 3 kg",
                    "flat_label": None,
                    "status": "expected",
                },
                {
                    "donation_id": str(toys),
                    "items": "toys 2",
                    "flat_label": None,
                    "status": "expected",
                },
            ],
            "bags": [],
        }
        assert views[str(kunj)]["pickups"]["bags"] == [
            {"order_id": str(bag), "flat_label": None, "status": "expected"}
        ]
        assert views[str(green)]["pickups"] == {
            "empties_collected": None,
            "donations": [],
            "bags": [],
        }

        # A partial report: the empties and one donation. A replay answers the same, once.
        idem = key()
        first = await report(
            api,
            rider,
            sunrise,
            {"empties": 2, "donations": [{"donation_id": str(clothes), "status": "collected"}]},
            idem=idem,
        )
        assert first.status_code == 200, first.text
        assert first.json()["pickups"]["empties_collected"] == 2
        assert first.json()["pickups"]["donations"][0]["status"] == "collected"
        again = await report(
            api,
            rider,
            sunrise,
            {"empties": 2, "donations": [{"donation_id": str(clothes), "status": "collected"}]},
            idem=idem,
        )
        assert again.status_code == 200 and again.json() == first.json()
        # The other donation was not ready; the same status again changes nothing.
        late = await report(
            api,
            rider,
            sunrise,
            {"donations": [{"donation_id": str(toys), "status": "not_ready"}]},
        )
        assert late.status_code == 200, late.text
        same = await report(
            api,
            rider,
            sunrise,
            {"donations": [{"donation_id": str(clothes), "status": "collected"}]},
        )
        assert same.status_code == 200
        changed = await report(
            api,
            rider,
            sunrise,
            {"donations": [{"donation_id": str(clothes), "status": "not_ready"}]},
        )
        assert changed.status_code == 409
        assert changed.json()["code"] == "logistics.pickup_already_reported"
        assert changed.json()["donation_ids"] == [str(clothes)]

        # The wrong stop: the bag is Krishna Kunj's, the donation Sunrise's.
        wrong = await report(api, rider, sunrise, {"bags": [f"GB1:{bag.hex}"]})
        assert (wrong.status_code, wrong.json()["code"]) == (422, "logistics.pickup_unknown")
        assert wrong.json()["bags"] == [f"GB1:{bag.hex}"]
        wrong = await report(
            api, rider, kunj, {"donations": [{"donation_id": str(toys), "status": "collected"}]}
        )
        assert (wrong.status_code, wrong.json()["code"]) == (422, "logistics.pickup_unknown")
        assert wrong.json()["donation_ids"] == [str(toys)]
        assert (await report(api, rider, kunj, {})).status_code == 422

        taken = await report(api, rider, kunj, {"bags": [f"GB1:{bag.hex}"]})
        assert taken.status_code == 200, taken.text
        assert taken.json()["pickups"]["bags"][0]["status"] == "collected"

        # The events: what each report changed, no names, flats or phones.
        rider_id = str(await user_id(db_session, ARJUN))
        sent = await events(db_session)
        assert [(e["stop_id"], e["empties"], e["donations"], e["bag_order_ids"]) for e in sent] == [
            (str(sunrise), 2, [{"donation_id": str(clothes), "status": "collected"}], []),
            (str(sunrise), None, [{"donation_id": str(toys), "status": "not_ready"}], []),
            (str(kunj), None, [], [str(bag)]),
        ]
        assert {(e["rider_user_id"], e["delivery_date"], e["hub_id"]) for e in sent} == {
            (rider_id, DAY.isoformat(), str(SEED_HUB_ID))
        }
        taps = await db_session.scalar(
            select(func.count()).select_from(FieldEvent).where(FieldEvent.action == "pickup")
        )
        assert taps == 3

        # M9: collected → picked_up by the rider on the day; not ready → rolled a week. M6 skips
        # the bag (it has no packing task here), and the event is delivered all the same.
        await drain(db_session)
        rows = {
            d.id: d
            for d in await db_session.scalars(
                select(Donation).execution_options(populate_existing=True)
            )
        }
        assert (rows[clothes].status, rows[clothes].picked_up_on) == ("picked_up", DAY)
        assert str(rows[clothes].picked_up_by_user_id) == rider_id
        assert (rows[toys].status, rows[toys].pickup_on) == ("scheduled", NEXT_TUESDAY)
        unsent = await db_session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.event_type == "logistics.pickup_completed",
                OutboxEvent.sent_at.is_(None),
            )
        )
        assert unsent == 0

        # The hub's list: flat labels for hub staff, totals by status.
        manager = await as_user(api, google, VIKRAM)
        listed = await api.get(HUB, params={"date": DAY.isoformat()}, headers=manager)
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert listed.headers["cache-control"] == "no-store"
        assert body["totals"] == {
            "empties": {"expected": 0, "collected": 2},
            "donations": {"expected": 0, "collected": 1, "not_ready": 1},
            "bags": {"expected": 0, "collected": 1, "not_ready": 0},
        }
        by_stop = {i["stop_id"]: i for i in body["items"]}
        assert set(by_stop) == {str(sunrise), str(kunj), str(green)}
        labels = {d["flat_label"] for d in by_stop[str(sunrise)]["pickups"]["donations"]}
        assert all(label and "," in label for label in labels)
        assert by_stop[str(kunj)]["pickups"]["bags"][0]["flat_label"]
        assert by_stop[str(sunrise)]["vehicle"]["label"] == "EV-1"

        # A skipped stop takes nothing; at completion what is left was not ready.
        skipped = await api.post(
            f"/v1/stops/{green}/skip", json={"reason": "no_access"}, headers={**rider, **key()}
        )
        assert skipped.status_code == 200
        refused = await report(api, rider, green, {"empties": 1})
        assert (refused.status_code, refused.json()["code"]) == (409, "logistics.stop_skipped")
        for stop in (sunrise, kunj):
            await api.post(
                f"/v1/stops/{stop}/skip", json={"reason": "other"}, headers={**rider, **key()}
            )
        done = await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
        assert done.status_code == 200, done.text
        left = dict(
            (
                await db_session.execute(
                    select(Pickup.stop_id, Pickup.status)
                    .where(Pickup.kind == "empties")
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        assert left == {sunrise: "collected", kunj: "not_ready", green: "not_ready"}
        closed = await report(api, rider, sunrise, {"empties": 3})
        assert (closed.status_code, closed.json()["code"]) == (409, "logistics.route_closed")
        assert len(await events(db_session)) == 3


async def test_only_the_routes_rider_reports_and_hub_managers_and_admins_list(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day = await published_day(db_session)
        await pack(db_session, *day.orders.values())
        rider = await as_user(api, google, ARJUN)
        stop_id = day.stop_ids[0]
        early = await report(api, rider, stop_id, {"empties": 1})
        assert (early.status_code, early.json()["code"]) == (409, "logistics.route_not_started")
        begun = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
        assert begun.status_code == 200

        async def rider_of(hub: uuid.UUID, phone: str) -> dict[str, str]:
            user, _ = await find_or_create_user(db_session, phone)
            db_session.add(UserRole(user_id=user.id, role="rider", scope_type="hub", scope_id=hub))
            await db_session.commit()
            return await as_user(api, google, phone)

        other = await rider_of(SEED_HUB_ID, "+919800000951")
        theirs = await report(api, other, stop_id, {"empties": 1})
        assert (theirs.status_code, theirs.json()["code"]) == (403, "logistics.not_your_route")
        elsewhere = await rider_of(OTHER_HUB, "+919800000952")
        lost = await report(api, elsewhere, stop_id, {"empties": 1})
        assert (lost.status_code, lost.json()["code"]) == (404, "logistics.stop_not_found")
        for phone in (VIKRAM, MEENA):
            denied = await report(api, await as_user(api, google, phone), stop_id, {"empties": 1})
            assert (denied.status_code, denied.json()["code"]) == (403, "auth.forbidden")
        no_key = await api.post(f"/v1/stops/{stop_id}/pickups", json={"empties": 1}, headers=rider)
        assert no_key.status_code == 400
        ok = await report(api, rider, stop_id, {"empties": 1})
        assert ok.status_code == 200, ok.text

        for phone, expected in ((VIKRAM, 200), (NEHA, 200), (SANJAY, 403), (ARJUN, 403)):
            got = await api.get(HUB, headers=await as_user(api, google, phone))
            assert got.status_code == expected, (phone, got.text)
        manager = await as_user(api, google, VIKRAM)
        other_hub = await api.get(f"/v1/hubs/{OTHER_HUB}/pickups", headers=manager)
        assert other_hub.status_code == 403
        far = await api.get(HUB, params={"date": "2026-11-30"}, headers=manager)
        assert far.json()["code"] == "logistics.date_out_of_range"
        tomorrow = await api.get(HUB, params={"date": "2026-10-14"}, headers=manager)
        assert tomorrow.json()["items"] == []


async def test_open_empties_are_handover_crates_less_collected_and_go_to_the_next_solve(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(MORNING, tick=False):
        day = await started(api, google, db_session)
        rider = await as_user(api, google, ARJUN)
        captain = await as_user(api, google, MEENA)
        sunrise = await stop_of(db_session, day, SEED_SOCIETY_ID)
        green = await stop_of(db_session, day, GREEN_VALLEY)
        sunrise_id, crates = sunrise.id, sunrise.crates
        green_id, green_load = green.id, {"crates": green.crates, "bags": green.bags}
        handed = await api.post(
            f"/v1/stops/{sunrise.id}/handover",
            json={
                "crates": sunrise.crates,
                "bags": sunrise.bags,
                "handover_code": sunrise.handover_code,
            },
            headers={**captain, **key()},
        )
        assert handed.status_code == 200, handed.text
        # Green Valley has no captain: the rider hands over to himself and keeps his crates.
        rider_mode = await api.post(
            f"/v1/stops/{green_id}/handover",
            json=green_load,
            headers={**rider, **key()},
        )
        assert rider_mode.status_code == 200, rider_mode.text
        assert crates >= 1
        assert await pickups.open_empties(db_session, [SEED_SOCIETY_ID, GREEN_VALLEY]) == {
            SEED_SOCIETY_ID: crates,
            GREEN_VALLEY: 0,
        }

        # The next solve carries them on Sunrise's stop.
        await locked_order(db_session, PRIYA, await slot(db_session, time(8)), date(2026, 10, 14))
        stops, _, _ = await solver._stops(db_session, SEED_HUB_ID, date(2026, 10, 14))  # pyright: ignore[reportPrivateUsage]
        assert [s.empties for s in stops.values() if s.society_id == SEED_SOCIETY_ID] == [crates]

        # Taking one back leaves the rest open.
        took = await report(api, rider, sunrise_id, {"empties": 1})
        assert took.status_code == 200, took.text
        assert await pickups.open_empties(db_session, [SEED_SOCIETY_ID]) == {
            SEED_SOCIETY_ID: crates - 1
        }


async def test_m6_unpacks_a_returned_bag_from_the_event_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(PACKING_MORNING, tick=False):
        order_id, _, _ = await missed_bag(api, google, db_session)
        rider = await user_id(db_session, ARJUN)
        event = OutboxEvent(
            event_type="logistics.pickup_completed",
            payload={
                "hub_id": str(SEED_HUB_ID),
                "rider_user_id": str(rider),
                "delivery_date": DAY.isoformat(),
                "donations": [],
                "bag_order_ids": [str(order_id), str(uuid.uuid4())],  # the second is no bag
            },
        )
        for _ in range(2):  # a redelivered event unpacks nothing twice
            await hub_jobs.on_pickup_completed(db_session, event)
            kept = list(await db_session.scalars(select(KeptItem)))
            assert len(kept) == 2 and {k.bag_order_id for k in kept} == {order_id}
