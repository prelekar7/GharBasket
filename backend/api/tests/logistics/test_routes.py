"""Routes (M7 §3.3): riders read their own published routes, hub staff the hub's; hub managers
change the rider or the vehicle before the start."""

import uuid
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import Route, RouteStop
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    DAY,
    KAVITA,
    MORNING,
    PRIYA,
    SANJAY,
    VIKRAM,
    as_user,
    published_day,
    user_id,
    vehicle,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")


def by_society(route: dict[str, Any], society_id: uuid.UUID) -> dict[str, Any]:
    [stop] = [s for s in route["stops"] if s["society"]["id"] == str(society_id)]
    return stop


@time_machine.travel(MORNING, tick=False)
async def test_the_rider_and_the_hub_staff_read_the_route_with_its_stops(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    day = await published_day(db_session)
    arjun = await user_id(db_session, ARJUN)
    api = clients()
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    path = f"/v1/routes/{day.route_id}"

    mine = await api.get("/v1/riders/me/routes", headers=rider)
    assert mine.status_code == 200 and mine.headers["Cache-Control"] == "no-store"
    [summary] = mine.json()["items"]
    assert (summary["id"], summary["status"], summary["rider"]["user_id"]) == (
        str(day.route_id),
        "published",
        str(arjun),
    )
    assert (summary["stops_total"], summary["stops_done"], summary["bags"]) == (3, 0, 4)
    tomorrow = await api.get("/v1/riders/me/routes", params={"date": "2026-10-14"}, headers=rider)
    assert tomorrow.json()["items"] == []
    hub = await api.get(
        f"/v1/hubs/{SEED_HUB_ID}/routes", params={"date": DAY.isoformat()}, headers=manager
    )
    assert [r["id"] for r in hub.json()["items"]] == [str(day.route_id)]
    for query in ({"status": "in_progress"}, {"kind": "tiffin"}):
        none = await api.get(f"/v1/hubs/{SEED_HUB_ID}/routes", params=query, headers=manager)
        assert none.json()["items"] == []

    read = await api.get(path, headers=rider)
    assert read.status_code == 200 and read.headers["ETag"] == '"2"'
    detail = read.json()
    assert (detail["source"], detail["left_behind"], detail["stops_total"]) == (
        "fallback_nearest",
        [],
        3,
    )
    sunrise, green = by_society(detail, SEED_SOCIETY_ID), by_society(detail, GREEN_VALLEY)
    assert sunrise["society"]["name"] == "Sunrise Towers" and sunrise["slot"]["start"] == "07:00"
    assert sunrise["captain"]["display_name"] and not sunrise["rider_mode"]
    assert (green["captain"], green["rider_mode"]) == (None, True)
    assert sunrise["navigate_url"].startswith("https://www.google.com/maps/dir/?api=1&destination=")
    assert (sunrise["redelivery_bags"], sunrise["handed_over_at"]) == (0, None)
    # The handover codes and the live ETAs exist only while the route is in progress.
    assert all(s["handover_code"] is None and s["eta_now"] is None for s in detail["stops"])
    await db_session.execute(
        update(Route).where(Route.id == day.route_id).values(status="in_progress")
    )
    await db_session.execute(
        update(RouteStop).where(RouteStop.route_id == day.route_id).values(handover_code="4821")
    )
    await db_session.commit()
    running = (await api.get(path, headers=manager)).json()
    assert all(s["handover_code"] == "4821" for s in running["stops"])
    assert all(s["eta_now"] == s["eta"] for s in running["stops"])


@time_machine.travel(MORNING, tick=False)
async def test_another_riders_route_is_hidden_and_other_roles_are_refused(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    day = await published_day(db_session)
    api = clients()
    rider, packer = await as_user(api, google, ARJUN), await as_user(api, google, KAVITA)
    owner, customer = await as_user(api, google, SANJAY), await as_user(api, google, PRIYA)
    path = f"/v1/routes/{day.route_id}"
    await db_session.execute(
        update(Route).where(Route.id == day.route_id).values(rider_user_id=uuid.uuid4())
    )
    await db_session.commit()

    hidden = await api.get(path, headers=rider)
    assert (hidden.status_code, hidden.json()["code"]) == (404, "logistics.route_not_found")
    assert (await api.get("/v1/riders/me/routes", headers=rider)).json()["items"] == []
    for who in (packer, owner):
        assert (await api.get(path, headers=who)).status_code == 200
        assert (await api.get(f"/v1/hubs/{SEED_HUB_ID}/routes", headers=who)).status_code == 200
    for refused in (
        await api.get(path, headers=customer),
        await api.get(f"/v1/hubs/{SEED_HUB_ID}/routes", headers=rider),
        await api.get("/v1/riders/me/routes", headers=owner),
        await api.patch(path, json={}, headers={**owner, "If-Match": '"2"'}),
    ):
        assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")
    missing = await api.get(f"/v1/routes/{uuid.uuid4()}", headers=packer)
    assert missing.status_code == 404
    far = await api.get(
        f"/v1/hubs/{SEED_HUB_ID}/routes", params={"date": "2026-10-01"}, headers=owner
    )
    assert (far.status_code, far.json()["code"]) == (422, "logistics.date_out_of_range")
    yesterday = await api.get("/v1/riders/me/routes", params={"date": "2026-10-12"}, headers=rider)
    assert yesterday.status_code == 422
    # A draft route (its plan not published) is not the rider's to see yet.
    await db_session.execute(
        update(Route)
        .where(Route.id == day.route_id)
        .values(status="draft", rider_user_id=await user_id(db_session, ARJUN))
    )
    await db_session.commit()
    assert (await api.get(path, headers=rider)).status_code == 404


@time_machine.travel(MORNING, tick=False)
async def test_a_hub_manager_changes_the_rider_and_the_vehicle_before_the_start(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    day = await published_day(db_session)
    arjun = await user_id(db_session, ARJUN)
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    path = f"/v1/routes/{day.route_id}"

    missing = await api.patch(path, json={"rider_user_id": None}, headers=manager)
    assert missing.status_code == 428
    stale = await api.patch(
        path, json={"rider_user_id": None}, headers={**manager, "If-Match": '"1"'}
    )
    assert stale.status_code == 412
    kavita = await user_id(db_session, KAVITA)
    not_rider = await api.patch(
        path, json={"rider_user_id": str(kavita)}, headers={**manager, "If-Match": '"2"'}
    )
    assert (not_rider.status_code, not_rider.json()["code"]) == (422, "logistics.rider_invalid")
    null_van = await api.patch(
        path, json={"vehicle_id": None}, headers={**manager, "If-Match": '"2"'}
    )
    assert null_van.status_code == 422
    bike = await vehicle(db_session, "Bike", kind="two_wheeler")
    tiny = await vehicle(db_session, "Mini", kg=10, crates=1)
    for vehicle_id, status, code in (
        (bike.id, 422, "request.invalid"),
        (uuid.uuid4(), 404, "logistics.vehicle_not_found"),
        (day.vehicle_id, 200, None),  # unchanged: nothing to check
        (tiny.id, 422, "logistics.vehicle_too_small"),
    ):
        answer = await api.patch(
            path, json={"vehicle_id": str(vehicle_id)}, headers={**manager, "If-Match": '"2"'}
        )
        if code is None:
            assert answer.status_code == 200 and answer.headers["ETag"] == '"2"'
        else:
            assert (answer.status_code, answer.json()["code"]) == (status, code), answer.text

    ev2 = await vehicle(db_session, "EV-2")
    changed = await api.patch(
        path,
        json={"rider_user_id": None, "vehicle_id": str(ev2.id)},
        headers={**manager, "If-Match": '"2"'},
    )
    assert changed.status_code == 200, changed.text
    assert changed.headers["ETag"] == '"3"'
    assert (changed.json()["rider"], changed.json()["vehicle"]["label"]) == (None, "EV-2")
    published = list(
        (
            await db_session.execute(
                select(OutboxEvent.payload)
                .where(OutboxEvent.event_type == "logistics.route_published")
                .order_by(OutboxEvent.id)
            )
        ).scalars()
    )
    assert (published[-1]["previous_rider_user_id"], published[-1]["rider_user_id"]) == (
        str(arjun),
        None,
    )
    [entry] = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.action == "routes.edited"))
    ).scalars()
    assert entry.after == {"rider_user_id": None, "vehicle_id": str(ev2.id)}
    # Back to Arjun: route_published again.
    again = await api.patch(
        path, json={"rider_user_id": str(arjun)}, headers={**manager, "If-Match": '"3"'}
    )
    assert again.status_code == 200 and again.json()["rider"]["user_id"] == str(arjun)

    for status, code in (
        ("in_progress", "logistics.routes_started"),
        ("completed", "logistics.route_closed"),
    ):
        await db_session.execute(
            update(Route).where(Route.id == day.route_id).values(status=status)
        )
        await db_session.commit()
        late = await api.patch(
            path, json={"rider_user_id": None}, headers={**manager, "If-Match": '"4"'}
        )
        assert (late.status_code, late.json()["code"]) == (409, code)
