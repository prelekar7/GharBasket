"""Tiffin offers (M8 §3.4, §6): the kitchen's view, accept and decline, expiry, and the 15-minute
job's next rounds until midnight."""

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import kitchens
from gharbasket.modules.partners.models import Assignment, AssignmentOrder, AssignmentRun
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import (
    SEED_HUB_ID,
)

from .. import seed_cache as cached
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, MEENA, PRIYA, SUNITA, VIKRAM, as_user
from .tiffins import EVENING, T, kitchen, kitchen_row, tag, tiffin_order

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("tiffin_world")]

RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"


@pytest.fixture
async def tiffin_world(db_session: AsyncSession) -> None:
    await cached.seed(db_session)
    await cached.seed_societies(db_session)
    await cached.seed_households(db_session)
    await cached.seed_catalog(db_session)
    await cached.seed_hubs(db_session)
    await cached.seed_partners(db_session)
    await tag(db_session, "tomato", "veg")
    await tag(db_session, "potato", "veg", "jain")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    at = [EVENING]
    monkeypatch.setattr(kitchens, "now", lambda: at[0])
    return at


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


async def test_the_kitchen_reads_accepts_and_declines_and_released_orders_go_to_the_next_round(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata_auth, _ = await as_user(api, google, LATA)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    sunita = await kitchen(db_session, SUNITA, "Sunita tai", lunch=3)
    sunita_auth, _ = await as_user(api, google, SUNITA)
    first = await tiffin_order(db_session, PRIYA, 3)
    second = await tiffin_order(db_session, MEENA, 3)
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    mine = await api.get(f"/v1/assignments?date={T}", headers=lata_auth)
    assert mine.status_code == 200, mine.text
    (offer,) = mine.json()["items"]
    assert offer["partner"] == {"id": str(lata), "display_name": "Lata tai"}
    assert (offer["status"], offer["portions"], offer["ready_by"]) == ("offered", 3, "11:30")
    assert offer["slot"]["start"] == "12:30" and offer["pickup_qr"] is None
    assert offer["dishes"][0]["portions"] == 3 and offer["dishes"][0]["name"]
    assert offer["orders"][0]["society"]["name"] == "Sunrise Towers"
    assert offer["orders"][0]["short_code"].startswith("T-")
    assert (await api.get("/v1/assignments?date=2027-01-01", headers=lata_auth)).status_code == 422
    theirs = (await api.get(f"/v1/assignments?date={T}", headers=sunita_auth)).json()["items"][0]

    url = f"/v1/assignments/{offer['id']}"
    accept_key = {**lata_auth, **key()}
    accepted = await api.post(f"{url}/accept", headers=accept_key)
    replayed = await api.post(f"{url}/accept", headers=accept_key)
    twice = await api.post(f"{url}/accept", headers={**lata_auth, **key()})
    stranger = await api.post(
        f"/v1/assignments/{theirs['id']}/accept", headers={**lata_auth, **key()}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted" and accepted.json()["accepted_at"]
    assert replayed.headers.get("Idempotent-Replayed") == "true"
    assert (twice.status_code, twice.json()["code"]) == (409, "partners.assignment_closed")
    assert (stranger.status_code, stranger.json()["code"]) == (404, "partners.assignment_not_found")

    declined = await api.post(
        f"/v1/assignments/{theirs['id']}/decline",
        json={"reason": "unwell", "note": "fever"},
        headers={**sunita_auth, **key()},
    )
    assert declined.json()["status"] == "declined"
    assert {o["status"] for o in declined.json()["orders"]} == {"released"}
    released = await events(db_session, "partners.assignment_released")
    assert released == [
        {
            "assignment_id": theirs["id"],
            "partner_id": str(sunita),
            "hub_id": str(SEED_HUB_ID),
            "date": T.isoformat(),
            "meal": "lunch",
            "order_ids": [o["order_id"] for o in theirs["orders"]],
            "reason": "declined",
        }
    ]
    # Round 2 leaves Sunita out; Lata is full, so the released order stays unassigned.
    again = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    assert (again.json()["offers"], again.json()["unassigned"]) == (0, 1)
    rounds = (await db_session.execute(select(AssignmentRun.round))).scalars().all()
    assert sorted(rounds) == [1, 2]
    offered = {uuid.UUID(o["order_id"]) for a in (offer, theirs) for o in a["orders"]}
    assert offered == {first, second}


async def test_unanswered_offers_expire_and_the_15_minute_job_runs_the_next_round_until_midnight(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order_id = await tiffin_order(db_session, PRIYA, 2)

    first = await api.post(
        "/v1/internal/jobs/assign_kitchens", json={"scheduled_for": EVENING.isoformat()}
    )
    assert first.json()["summary"] == {
        "delivery_date": T.isoformat(),
        "runs": 1,
        "offers": 1,
        "unassigned": 0,
    }
    early = await api.post(
        "/v1/internal/jobs/assignment_acceptance_timeout",
        json={"scheduled_for": (EVENING + timedelta(minutes=15)).isoformat()},
    )
    assert early.json()["summary"] == {
        "delivery_date": T.isoformat(),
        "expired": 0,
        "runs": 0,
        "offers": 0,
        "unassigned": 0,
    }
    sunita = await kitchen(db_session, SUNITA, "Sunita tai", lunch=3)
    late = await api.post(
        "/v1/internal/jobs/assignment_acceptance_timeout",
        json={"scheduled_for": (EVENING + timedelta(hours=1)).isoformat()},  # 22:30 IST
    )
    assert late.json()["summary"]["expired"] == 1
    assert late.json()["summary"]["offers"] == 1  # round 2, to Sunita: Lata let hers expire
    rows = (await db_session.execute(select(Assignment).order_by(Assignment.round))).scalars().all()
    assert [(a.partner_id, a.status, a.round) for a in rows] == [
        (lata, "expired", 1),
        (sunita, "offered", 2),
    ]
    assert rows[1].respond_by == EVENING + timedelta(hours=1, minutes=30)
    assert (await events(db_session, "partners.assignment_released"))[0]["reason"] == "expired"
    after_midnight = await api.post(
        "/v1/internal/jobs/assignment_acceptance_timeout",
        json={"scheduled_for": (EVENING + timedelta(hours=3)).isoformat()},  # T 00:30 IST
    )
    assert after_midnight.json()["summary"] == {"delivery_date": T.isoformat(), "expired": 1}
    held = (await db_session.execute(select(AssignmentOrder.status))).scalars().all()
    assert sorted(held) == ["released", "released"] and order_id
    assert (await kitchen_row(db_session, lata)).version == 1
