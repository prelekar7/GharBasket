"""Tiffin assignment rounds (M8 §3.4, §6): M8's greedy fallback and M10's answer, one offer per
kitchen, re-runs and the 21:30 job."""

import uuid
from datetime import datetime, time, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.partners import kitchens
from gharbasket.modules.partners.models import Assignment, AssignmentOrder, AssignmentRun
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import (
    SEED_HUB_ID,
    seed,
    seed_catalog,
    seed_households,
    seed_hubs,
    seed_partners,
    seed_societies,
)

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, MEENA, PRIYA, RAMESH, SUNITA, VIKRAM, as_user
from .tiffins import EVENING, FAR, KRISHNA, T, kitchen, tag, tiffin_order

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("tiffin_world")]

RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"


@pytest.fixture
async def tiffin_world(db_session: AsyncSession) -> None:
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_hubs(db_session)
    await seed_partners(db_session)
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


async def test_a_round_offers_each_kitchen_its_orders_once_and_records_the_run(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=4)
    sunita = await kitchen(db_session, SUNITA, "Sunita tai", at=KRISHNA, lunch=3)
    await kitchen(db_session, RAMESH, "Far away", at=FAR)
    priya = await tiffin_order(db_session, PRIYA, 3)
    meena = await tiffin_order(db_session, MEENA, 1)
    ramesh = await tiffin_order(db_session, RAMESH, 2)
    jain = await tiffin_order(db_session, PRIYA, 1, slug="potato")  # nobody cooks jain
    dinner = await tiffin_order(db_session, MEENA, 2, start=time(19, 30))

    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    again = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert (body["offers"], body["unassigned"], len(body["run_ids"])) == (2, 2, 2)
    # Offered orders are not offered again; the two nobody could take are tried again.
    assert (again.json()["offers"], again.json()["unassigned"]) == (0, 2)
    rows = {a.partner_id: a for a in (await db_session.execute(select(Assignment))).scalars()}
    assert set(rows) == {lata, sunita}
    # Largest first: Priya's 3 to Lata (nearest), Ramesh's 2 to Sunita (Krishna Kunj); Meena's 1
    # fits Lata (1 left).
    assert (rows[lata].portions, rows[sunita].portions) == (4, 2)
    assert {(a.status, a.source, a.round, a.meal) for a in rows.values()} == {
        ("offered", "fallback", 1, "lunch")
    }
    assert rows[lata].respond_by == EVENING + timedelta(hours=1)  # 22:30 IST
    held = (await db_session.execute(select(AssignmentOrder))).scalars().all()
    assert {h.order_id for h in held} == {priya, meena, ramesh}
    assert sorted(h.short_code for h in held) == ["T-01", "T-02", "T-03"]
    runs = await db_session.execute(
        select(AssignmentRun).where(AssignmentRun.round == 1).order_by(AssignmentRun.meal)
    )
    dinner_run, lunch_run = list(runs.scalars())
    assert (lunch_run.source, lunch_run.model, lunch_run.portions) == ("fallback", "greedy-m8", 6)
    assert lunch_run.unassigned == [{"order_id": str(jain), "reason": "no_skills"}]
    assert dinner_run.unassigned == [{"order_id": str(dinner), "reason": "no_room"}]
    assert lunch_run.summary["kitchens_used"] == 2
    created = await events(db_session, "partners.assignment_created")
    assert sorted(e["portions"] for e in created) == [2, 4]
    assert {e["round"] for e in created} == {1} and created[0]["meal"] == "lunch"
    unassigned = await events(db_session, "partners.tiffins_unassigned")
    assert [(e["meal"], e["order_ids"]) for e in unassigned[:2]] == [
        ("dinner", [str(dinner)]),
        ("lunch", [str(jain)]),
    ]
    # Lata's capacity now shows the offered portions.
    lata_auth, _ = await as_user(api, google, LATA)
    capacity = await api.get(f"/v1/kitchen-capacity?from={T}&to={T}", headers=lata_auth)
    assert capacity.json()["items"][0]["assigned_portions"] == 4


async def test_m10s_answer_is_used_with_the_specs_parameters(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients(ml_service_url="http://ml.test")
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    priya = await tiffin_order(db_session, PRIYA, 2)
    meena = await tiffin_order(db_session, MEENA, 1)
    seen: list[intel_api.AssignRequest] = []

    async def solve(
        http: httpx2.AsyncClient, settings: Any, request: intel_api.AssignRequest, **kw: Any
    ) -> intel_api.KitchenAssignment:
        seen.append(request)
        return intel_api.KitchenAssignment.model_validate(
            {
                "status": "partial",
                "assignments": [
                    {"order_id": str(priya), "kitchen_id": str(lata), "portions": 2, "km": 0.4}
                ],
                "unassigned": [{"order_id": str(meena), "reason": "out_of_reach"}],
                "summary": None,
                "seconds": 0.2,
                "model": "cp-sat-v1",
            }
        )

    monkeypatch.setattr(intel_api, "assign_kitchens", solve)
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert ran.json()["offers"] == 1 and ran.json()["unassigned"] == 1
    (request,) = seen
    assert request.fairness_lambda == 300 and request.max_km == 5 and request.rating_floor == 3.8
    assert [(k.id, k.max_portions, k.rating) for k in request.kitchens] == [(str(lata), 5, 4.5)]
    assert sorted(o.portions for o in request.orders) == [1, 2]
    run = (await db_session.execute(select(AssignmentRun))).scalar_one()
    assert (run.source, run.model, run.status) == ("ml", "cp-sat-v1", "partial")
    assignment = (await db_session.execute(select(Assignment))).scalar_one()
    assert (assignment.source, assignment.portions) == ("ml", 2)


async def test_the_2130_job_runs_round_1_for_tomorrow_at_every_active_hub(
    clients: ClientFactory, db_session: AsyncSession
) -> None:
    api = clients()
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    await tiffin_order(db_session, PRIYA, 2)

    ran = await api.post(
        "/v1/internal/jobs/assign_kitchens", json={"scheduled_for": EVENING.isoformat()}
    )

    assert ran.json()["summary"] == {
        "delivery_date": T.isoformat(),
        "runs": 1,
        "offers": 1,
        "unassigned": 0,
    }
    offer = (await db_session.execute(select(Assignment))).scalar_one()
    assert offer.respond_by == EVENING + timedelta(hours=1)  # the scheduled time, not the clock
