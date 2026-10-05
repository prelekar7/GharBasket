"""The pantry twin in the weekly draft (M3 §3.1 step 2, M10 §3.3): after a locked box and the
household's taps, the next week's daily-use lines come from the twin."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway.models import Prediction
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.modules.subscriptions import service

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import GENERATE_AT, NOW, PRIYA, as_user, subscribe

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]


async def generate(api: httpx2.AsyncClient, at: str) -> dict[str, Any]:
    answer = await api.post("/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": at})
    assert answer.status_code == 200, answer.text
    return answer.json()["summary"]


@time_machine.travel(NOW, tick=False)
async def test_the_second_week_uses_the_twin_after_taps(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api, GENERATE_AT)  # the box for Tue 13 Oct: the rule table alone
    first = (await api.get("/v1/cycles/current", headers=priya)).json()
    milk = next(line for line in first["lines"] if line["slug"] == "milk")
    assert milk["reason_code"] == "daily_use" and milk["source"] == "rule"
    approved = await api.post(
        f"/v1/cycles/{first['id']}/approve",
        headers={**priya, "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text
    await subscriptions_api.mark_locked(db_session, uuid.UUID(first["id"]), uuid.uuid4())

    # Delivered on the 13th; the household says the milk ran out and the curd was wasted.
    with time_machine.travel(datetime(2026, 10, 14, 8, 0, tzinfo=NOW.tzinfo), tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        curd = next(line for line in first["lines"] if line["slug"] == "curd")
        taps = await api.post(
            f"/v1/cycles/{first['id']}/feedback",
            json={
                "items": [
                    {"product_id": milk["product_id"], "signal": "ran_out"},
                    {"product_id": curd["product_id"], "signal": "wasted"},
                ]
            },
            headers={**priya, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert taps.status_code == 201, taps.text
        observed = await service.pantry_observations(
            db_session, uuid.UUID(first["household_id"]), date(2026, 10, 20)
        )
        assert observed[uuid.UUID(milk["product_id"])] == [(milk["qty"] * 500.0, "ran_out")]
        assert observed[uuid.UUID(curd["product_id"])][0][1] == "wasted"

        # The next week's draft (Sat 17 Oct 18:00 IST for Tue 20 Oct).
        await generate(api, (datetime.fromisoformat(GENERATE_AT) + timedelta(days=7)).isoformat())
    cycles = (await db_session.execute(select(service.WeeklyCycle))).scalars().all()
    second = next(c for c in cycles if c.delivery_date == date(2026, 10, 20))
    lines = {line.product_id: line for line in await service.lines_of(db_session, second.id)}
    milk_line = lines[uuid.UUID(milk["product_id"])]
    curd_line = lines[uuid.UUID(curd["product_id"])]
    assert milk_line.reason_code == "pantry_low" and milk_line.source == "ai"
    assert milk_line.reason_params is not None and curd_line.reason_params is not None
    assert milk_line.reason_params["weeks"] == 1
    assert milk_line.reason_params["source"] == "fallback"  # no ML service in tests
    # Ran out pulls the mean above the box's quantity; wasted pulls it below.
    assert milk_line.reason_params["mean"] > first_total(first, "milk")
    assert curd_line.reason_params["mean"] < first_total(first, "curd")
    kept = (
        (await db_session.execute(select(Prediction).where(Prediction.kind == "pantry")))
        .scalars()
        .all()
    )
    assert len(kept) == 1 and kept[0].for_date == date(2026, 10, 20)


def first_total(cycle: dict[str, Any], slug: str) -> float:
    line = next(line for line in cycle["lines"] if line["slug"] == slug)
    return line["qty"] * line["step"]["qty"] * (1000 if line["step"]["unit"] in ("kg", "l") else 1)


@time_machine.travel(NOW, tick=False)
async def test_learned_cohort_priors_replace_the_rule_table_in_the_twin(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """v1 (M10 §3.3): once the refit job stored priors for the household's cohort, the twin
    starts from them instead of the family-size table and says so in the line's params."""
    from gharbasket.modules.identity import api as identity_api
    from gharbasket.modules.subscriptions import rules
    from gharbasket.modules.subscriptions.rules import Member

    api = clients()
    priya, user_id = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api, GENERATE_AT)
    first = (await api.get("/v1/cycles/current", headers=priya)).json()
    milk = next(line for line in first["lines"] if line["slug"] == "milk")
    approved = await api.post(
        f"/v1/cycles/{first['id']}/approve",
        headers={**priya, "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text
    await subscriptions_api.mark_locked(db_session, uuid.UUID(first["id"]), uuid.uuid4())

    household = await identity_api.household_for_user(db_session, user_id)
    assert household is not None
    profile = await identity_api.household_plan_profile(db_session, household.id)
    assert profile is not None
    cohort = rules.cohort_of(rules.adult_equivalents([Member(m) for m in profile.member_age_bands]))
    # A prior far above what the box carried, with a heavy weight: the mean must move up.
    huge = milk["qty"] * 500.0 * 3
    stored = await api.post(
        "/v1/internal/intel/pantry-priors",
        json={
            "version": "2026-10-05",
            "items": [
                {"cohort": cohort, "product": "milk", "prior_mean": huge, "prior_weeks": 20.0}
            ],
        },
    )
    assert stored.status_code == 200 and stored.json()["accepted"] == 1

    with time_machine.travel(datetime(2026, 10, 17, 12, 30, tzinfo=NOW.tzinfo), tick=False):
        await generate(api, "2026-10-17T12:30:00Z")
    second = next(
        c
        for c in (await db_session.execute(select(service.WeeklyCycle))).scalars()
        if c.delivery_date == date(2026, 10, 20)
    )
    lines = {line.product_id: line for line in await service.lines_of(db_session, second.id)}
    milk_line = lines[uuid.UUID(milk["product_id"])]
    curd_line = next(
        line
        for line in lines.values()
        if line.reason_code == "pantry_low" and line is not milk_line
    )
    assert milk_line.reason_params is not None and curd_line.reason_params is not None
    assert (
        milk_line.reason_params["prior"] == "learned" and curd_line.reason_params["prior"] == "rule"
    )
    assert milk_line.reason_params["mean"] > milk["qty"] * 500.0 * 2
