"""The handover (M7 §3.4): the captain takes a stop's crates by count with the rider's code or a
photo, every dispatched order moving to at_society as hers; a mismatch moves only scanned bags;
rider mode for a society without a captain or a switched stop."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Captain
from gharbasket.modules.logistics.models import FieldEvent, Handover, RouteStop
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform.models import OutboxEvent
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
    Day,
    as_user,
    key,
    pack,
    published_day,
    user_id,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

SAVITA = "+919800001012"  # Krishna Kunj's captain

GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")


async def stop_of(db: AsyncSession, day: Day, society_id: uuid.UUID) -> RouteStop:
    stop = await db.scalar(
        select(RouteStop)
        .where(RouteStop.route_id == day.route_id, RouteStop.society_id == society_id)
        .execution_options(populate_existing=True)
    )
    assert stop is not None
    return stop


async def statuses(db: AsyncSession, *order_ids: uuid.UUID) -> list[str]:
    rows = dict(
        (
            await db.execute(
                select(Order.id, Order.status)
                .where(Order.id.in_(order_ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    return [rows[o] for o in order_ids]


async def started(api: Any, google: FakeGoogle, db: AsyncSession) -> Day:
    day = await published_day(db)
    await pack(db, *day.orders.values())
    rider = await as_user(api, google, ARJUN)
    answer = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert answer.status_code == 200, answer.text
    return day


@time_machine.travel(MORNING, tick=False)
async def test_the_captain_takes_the_crates_by_count_and_code(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await published_day(db_session)
    captain = await as_user(api, google, MEENA)
    sunrise = await stop_of(db_session, day, SEED_SOCIETY_ID)
    sunrise_id, code = sunrise.id, sunrise.handover_code
    path = f"/v1/stops/{sunrise_id}/handover"
    body = {"crates": 1, "bags": 2, "handover_code": "0000"}
    early = await api.post(path, json=body, headers={**captain, **key()})
    assert (early.status_code, early.json()["code"]) == (409, "logistics.route_not_started")

    await pack(db_session, *day.orders.values())
    rider = await as_user(api, google, ARJUN)
    assert (
        await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    ).status_code == 200
    code = (await stop_of(db_session, day, SEED_SOCIETY_ID)).handover_code
    assert code is not None

    wrong = {**body, "handover_code": "9999" if code != "9999" else "0000"}
    headers = {**captain, **key()}
    refused = await api.post(path, json=wrong, headers=headers)
    assert (refused.status_code, refused.json()["code"]) == (422, "logistics.code_invalid")
    assert (await api.post(path, json=wrong, headers=headers)).status_code == 422  # replay
    assert (await stop_of(db_session, day, SEED_SOCIETY_ID)).code_attempts == 1
    short = await api.post(
        path, json={**body, "bags": 1, "handover_code": code}, headers={**captain, **key()}
    )
    assert (short.status_code, short.json()["code"]) == (409, "logistics.count_mismatch")
    assert short.json()["expected"] == {"crates": 1, "bags": 2}

    done = await api.post(
        path,
        json={**body, "handover_code": code, "note": "at the gate"},
        headers={**captain, **key()},
    )
    assert done.status_code == 200, done.text
    out = done.json()
    assert (out["mismatch"], out["still_dispatched"], out["stop"]["status"]) == (
        False,
        [],
        "handed_over",
    )
    assert sorted(out["at_society"]) == sorted([str(day.orders[PRIYA]), str(day.orders[MEENA])])
    assert out["stop"]["handed_over_at"] is not None
    assert await statuses(db_session, day.orders[PRIYA], day.orders[MEENA]) == ["at_society"] * 2
    meena = await user_id(db_session, MEENA)
    moves = await db_session.execute(
        select(OrderEvent.actor, OrderEvent.actor_user_id).where(
            OrderEvent.to_status == "at_society"
        )
    )
    assert set(moves.all()) == {("captain", meena)}
    [handover] = (await db_session.execute(select(Handover))).scalars()
    assert (handover.by_actor, handover.proof, handover.partner_id is not None) == (
        "captain",
        "code",
        True,
    )
    [event] = (
        await db_session.execute(
            select(OutboxEvent.payload).where(
                OutboxEvent.event_type == "logistics.handover_completed"
            )
        )
    ).scalars()
    assert event["by_actor"] == "captain" and "handover_code" not in str(event)
    again = await api.post(path, json={**body, "handover_code": code}, headers={**captain, **key()})
    assert (again.status_code, again.json()["code"]) == (409, "logistics.already_handed_over")


@time_machine.travel(MORNING, tick=False)
async def test_a_count_mismatch_moves_only_the_scanned_bags(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await started(api, google, db_session)
    captain = await as_user(api, google, MEENA)
    sunrise = await stop_of(db_session, day, SEED_SOCIETY_ID)
    sunrise_id, code = sunrise.id, sunrise.handover_code
    priya = day.orders[PRIYA]
    done = await api.post(
        f"/v1/stops/{sunrise_id}/handover",
        json={
            "crates": 1,
            "bags": 1,
            "scanned_bags": [f"GB1:{priya.hex}"],
            "handover_code": code,
        },
        headers={**captain, **key()},
    )
    assert done.status_code == 200, done.text
    assert (done.json()["mismatch"], done.json()["at_society"]) == (True, [str(priya)])
    assert done.json()["still_dispatched"] == [str(day.orders[MEENA])]
    assert await statuses(db_session, priya, day.orders[MEENA]) == ["at_society", "dispatched"]


@time_machine.travel(MORNING, tick=False)
async def test_rider_mode_for_a_society_without_a_captain_or_a_switched_stop(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await started(api, google, db_session)
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    green = (await stop_of(db_session, day, GREEN_VALLEY)).id
    sunrise = (await stop_of(db_session, day, SEED_SOCIETY_ID)).id

    done = await api.post(
        f"/v1/stops/{green}/handover", json={"crates": 1, "bags": 1}, headers={**rider, **key()}
    )
    assert done.status_code == 200, done.text
    assert done.json()["at_society"] == [str(day.orders[SUNITA])]
    [handover] = (await db_session.execute(select(Handover))).scalars()
    assert (handover.by_actor, handover.proof) == ("rider", "rider_mode")

    body = {"crates": 1, "bags": 2}
    refused = await api.post(f"/v1/stops/{sunrise}/handover", json=body, headers={**rider, **key()})
    assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")
    path = f"/v1/stops/{sunrise}"
    version = (await api.get(f"/v1/routes/{day.route_id}", headers=manager)).headers["ETag"]
    stale = await api.patch(path, json={"rider_mode": True}, headers={**manager, "If-Match": '"1"'})
    assert stale.status_code == 412
    assert (await api.patch(path, json={"rider_mode": True}, headers=rider)).status_code == 403
    switched = await api.patch(
        path, json={"rider_mode": True}, headers={**manager, "If-Match": version}
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["rider_mode"] is True
    [tap] = (
        await db_session.execute(select(FieldEvent).where(FieldEvent.action == "rider_mode"))
    ).scalars()
    assert tap.payload == {"rider_mode": True}
    ok = await api.post(f"/v1/stops/{sunrise}/handover", json=body, headers={**rider, **key()})
    assert ok.status_code == 200, ok.text


@time_machine.travel(MORNING, tick=False)
async def test_an_ended_captain_wrong_codes_and_photos(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await started(api, google, db_session)
    captain = await as_user(api, google, MEENA)
    meena = await user_id(db_session, MEENA)
    sunrise = await stop_of(db_session, day, SEED_SOCIETY_ID)
    sunrise_id, code = sunrise.id, sunrise.handover_code
    path = f"/v1/stops/{sunrise_id}/handover"
    body: dict[str, Any] = {"crates": 1, "bags": 2}

    for wrong_proof in ({}, {"handover_code": "1234", "photo_key": "x"}):
        both = await api.post(path, json={**body, **wrong_proof}, headers={**captain, **key()})
        assert (both.status_code, both.json()["code"]) == (422, "request.invalid")
    await db_session.execute(
        update(RouteStop).where(RouteStop.id == sunrise_id).values(code_attempts=5)
    )
    await db_session.commit()
    locked = await api.post(
        path, json={**body, "handover_code": code}, headers={**captain, **key()}
    )
    assert (locked.status_code, locked.json()["code"]) == (429, "logistics.code_attempts_exceeded")
    other = f"delivery_photo/{uuid.uuid4()}/{uuid.uuid4()}.jpg"
    bad = await api.post(path, json={**body, "photo_key": other}, headers={**captain, **key()})
    assert (bad.status_code, bad.json()["code"]) == (422, "logistics.photo_key_invalid")

    await db_session.execute(
        update(Captain)
        .where(Captain.user_id == meena)
        .values(status="ended", until=datetime.now(UTC), end_reason="stepped_down")
    )
    await db_session.commit()
    photo = f"delivery_photo/{meena}/{uuid.uuid4()}.jpg"
    ended = await api.post(path, json={**body, "photo_key": photo}, headers={**captain, **key()})
    assert (ended.status_code, ended.json()["code"]) == (403, "logistics.not_society_captain")
    await db_session.execute(
        update(Captain)
        .where(Captain.user_id == meena)
        .values(status="active", until=None, end_reason=None)
    )
    await db_session.commit()
    # Her own key in the right shape, but nothing uploaded through M0: refused (until the
    # delivery_photo purpose lands every photo key is), so it cannot stand in for the code.
    invented = await api.post(path, json={**body, "photo_key": photo}, headers={**captain, **key()})
    assert (invented.status_code, invented.json()["code"]) == (422, "logistics.photo_key_invalid")
    await db_session.execute(
        update(RouteStop).where(RouteStop.id == sunrise_id).values(code_attempts=0)
    )
    await db_session.commit()
    savita = await as_user(api, google, SAVITA)  # Krishna Kunj's captain
    other = await api.post(path, json={**body, "handover_code": code}, headers={**savita, **key()})
    assert (other.status_code, other.json()["code"]) == (403, "auth.forbidden")
    ok = await api.post(path, json={**body, "handover_code": code}, headers={**captain, **key()})
    assert ok.status_code == 200, ok.text
    assert ok.json()["stop"]["handover_code"] is None  # the rider's code, not hers to see
    [handover] = (await db_session.execute(select(Handover))).scalars()
    assert (handover.proof, handover.photo_key) == ("code", None)
