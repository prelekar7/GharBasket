"""The captain's day and a stop's flats (M7 §3.4): the van's stops for her society, her flats by
tower and flat (labels only, no names or phones) with their counts; a rider sees a stop's flats
only on a rider-mode stop of his started route."""

import uuid
from datetime import UTC, datetime

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Captain
from gharbasket.modules.logistics.models import RouteStop
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    MEENA,
    MORNING,
    PRIYA,
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


@time_machine.travel(MORNING, tick=False)
async def test_the_captain_sees_the_van_her_flats_and_their_counts(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await published_day(db_session)
    await pack(db_session, *day.orders.values())
    captain = await as_user(api, google, MEENA)
    rider = await as_user(api, google, ARJUN)
    assert (
        await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    ).status_code == 200
    sunrise = await db_session.scalar(
        select(RouteStop).where(
            RouteStop.route_id == day.route_id, RouteStop.society_id == SEED_SOCIETY_ID
        )
    )
    assert sunrise is not None
    sunrise_id, code = sunrise.id, sunrise.handover_code

    today = await api.get("/v1/captains/me/today", headers=captain)
    assert today.status_code == 200, today.text
    assert today.headers["Cache-Control"] == "no-store"
    mine = today.json()
    assert mine["society"] == {"id": str(SEED_SOCIETY_ID), "name": "Sunrise Towers"}
    [stop] = mine["stops"]
    assert (stop["stop_id"], stop["status"], stop["expected"]) == (
        str(sunrise_id),
        "pending",
        {"crates": 1, "bags": 2, "redelivery_bags": 0},
    )
    assert stop["eta_now"] and stop["slot"]["start"] == "07:00"
    assert {f["order_id"] for f in mine["flats"]} == {
        str(day.orders[PRIYA]),
        str(day.orders[MEENA]),
    }
    assert all(
        set(f) == {"order_id", "flat_label", "kind", "bags", "redelivery", "status", "hold_until"}
        for f in mine["flats"]
    )
    labels = [f["flat_label"] for f in mine["flats"]]
    assert labels == sorted(labels) and all(labels)
    assert mine["counts"] == {
        "on_the_way": 2,
        "with_you": 0,
        "held": 0,
        "delivered": 0,
        "undelivered": 0,
    }

    handed = await api.post(
        f"/v1/stops/{sunrise_id}/handover",
        json={"crates": 1, "bags": 2, "handover_code": code},
        headers={**captain, **key()},
    )
    assert handed.status_code == 200, handed.text
    after = (await api.get("/v1/captains/me/today", headers=captain)).json()
    assert after["counts"]["with_you"] == 2 and after["stops"][0]["status"] == "handed_over"

    old = await api.get("/v1/captains/me/today", params={"date": "2026-10-11"}, headers=captain)
    assert (old.status_code, old.json()["code"]) == (422, "logistics.date_out_of_range")
    meena = await user_id(db_session, MEENA)
    await db_session.execute(
        update(Captain)
        .where(Captain.user_id == meena)
        .values(status="ended", until=datetime.now(UTC), end_reason="stepped_down")
    )
    await db_session.commit()
    ended = await api.get("/v1/captains/me/today", headers=captain)
    assert (ended.status_code, ended.json()["code"]) == (403, "logistics.not_society_captain")
    not_captain = await api.get("/v1/captains/me/today", headers=rider)
    assert (not_captain.status_code, not_captain.json()["code"]) == (403, "auth.forbidden")


@time_machine.travel(MORNING, tick=False)
async def test_a_stops_flats_for_the_rider_in_rider_mode_and_for_hub_staff(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await published_day(db_session)
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    stops = dict(
        (
            await db_session.execute(
                select(RouteStop.society_id, RouteStop.id).where(RouteStop.route_id == day.route_id)
            )
        ).all()
    )
    green, sunrise = stops[GREEN_VALLEY], stops[SEED_SOCIETY_ID]
    before = await api.get(f"/v1/stops/{green}/deliveries", headers=rider)
    assert before.status_code == 404  # not until the route has started

    await pack(db_session, *day.orders.values())
    assert (
        await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    ).status_code == 200
    flats = await api.get(f"/v1/stops/{green}/deliveries", headers=rider)
    assert flats.status_code == 200, flats.text
    [flat] = flats.json()["items"]
    assert (flat["order_id"], flat["status"], flat["kind"]) == (
        str(day.orders[SUNITA]),
        "on_the_way",
        "box",
    )
    hidden = await api.get(f"/v1/stops/{sunrise}/deliveries", headers=rider)
    assert hidden.status_code == 404  # a captain's stop: not the rider's to see
    staff = await api.get(f"/v1/stops/{sunrise}/deliveries", headers=manager)
    assert len(staff.json()["items"]) == 2
    missing = await api.get(f"/v1/stops/{uuid.uuid4()}/deliveries", headers=manager)
    assert (missing.status_code, missing.json()["code"]) == (404, "logistics.stop_not_found")
