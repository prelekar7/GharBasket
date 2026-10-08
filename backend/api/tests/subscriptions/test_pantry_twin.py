"""The pantry twin in the weekly draft (M3 §3.1 step 2, M10 §3.3): after a locked box and the
household's taps, the next week's perishable daily-use lines (milk, curd) come from the twin,
when it is switched on. It learns only the daily-use share of a box; the recipes come on top."""

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from gharbasket_ml import pantry as ml_pantry
from gharbasket_ml.schemas import PantryRequest as MlPantryRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.intel_gateway import fallback
from gharbasket.modules.intel_gateway.models import Prediction
from gharbasket.modules.intel_gateway.schemas import PantryItem, PantryObservation, PantryRequest
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.modules.subscriptions import planning, rules, service
from gharbasket.modules.subscriptions.rules import Member

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import GENERATE_AT, NOW, PRIYA, as_user, subscribe

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

FIRST = date(2026, 10, 13)
SECOND = date(2026, 10, 20)


async def generate(api: httpx2.AsyncClient, at: str) -> dict[str, Any]:
    answer = await api.post("/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": at})
    assert answer.status_code == 200, answer.text
    return answer.json()["summary"]


def total(cycle: dict[str, Any], slug: str) -> float:
    line = next(line for line in cycle["lines"] if line["slug"] == slug)
    return line["qty"] * line["step"]["qty"] * (1000 if line["step"]["unit"] in ("kg", "l") else 1)


async def first_week(
    api: httpx2.AsyncClient, google: FakeGoogle, db_session: AsyncSession, *, then_generate: bool
) -> dict[str, Any]:
    """Priya's first box (the rule table), approved and locked; delivered on the 13th, she says
    the milk ran out and the curd was wasted. With `then_generate`, the job drafts the next week."""
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api, GENERATE_AT)
    first = (await api.get("/v1/cycles/current", headers=priya)).json()
    milk = next(line for line in first["lines"] if line["slug"] == "milk")
    assert milk["reason_code"] == "daily_use" and milk["source"] == "rule"
    approved = await api.post(
        f"/v1/cycles/{first['id']}/approve",
        headers={**priya, "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text
    await subscriptions_api.mark_locked(db_session, uuid.UUID(first["id"]), uuid.uuid4())
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
        if then_generate:  # Sat 17 Oct 18:00 IST for Tue 20 Oct
            await generate(
                api, (datetime.fromisoformat(GENERATE_AT) + timedelta(days=7)).isoformat()
            )
    return first


async def world_of(
    db_session: AsyncSession, household_id: uuid.UUID
) -> tuple[identity_api.HouseholdPlanProfile, catalog_api.PlanCatalog]:
    profile = await identity_api.household_plan_profile(db_session, household_id)
    assert profile is not None and profile.hub_id is not None
    return profile, await catalog_api.plan_catalog(db_session, profile.hub_id)


async def cycle_on(db_session: AsyncSession, day: date) -> service.WeeklyCycle:
    cycles = (await db_session.execute(select(service.WeeklyCycle))).scalars().all()
    return next(c for c in cycles if c.delivery_date == day)


def expected_mean(
    profile: identity_api.HouseholdPlanProfile, slug: str, step: float, share: float, tap: str
) -> float:
    prior = rules.daily_use_plan(
        rules.adult_equivalents([Member(m) for m in profile.member_age_bands])
    )[slug]
    request = PantryRequest(
        items=[
            PantryItem(
                product=slug,
                step=step,
                prior_mean=prior,
                observations=[PantryObservation(qty=share, signal=tap)],  # pyright: ignore[reportArgumentType]
            )
        ]
    )
    return fallback.pantry_quantities(request).items[0].mean


async def pantry_predictions(db_session: AsyncSession) -> list[Prediction]:
    query = select(Prediction).where(Prediction.kind == "pantry")
    return list((await db_session.execute(query)).scalars().all())


@time_machine.travel(NOW, tick=False)
async def test_the_second_week_uses_the_twin_on_the_daily_use_share(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients(pantry_twin_enabled=True)
    first = await first_week(api, google, db_session, then_generate=True)
    profile, catalog = await world_of(db_session, uuid.UUID(first["household_id"]))
    milk, curd = catalog.product_by_slug("milk"), catalog.product_by_slug("curd")
    assert milk is not None and curd is not None

    # What the twin saw: the box's quantity minus what that week's recipes needed.
    first_need, _ = planning.recipe_need(
        profile, catalog, planning.plan_of((await cycle_on(db_session, FIRST)).meal_plan)
    )
    shares = {
        p.id: max(0.0, total(first, p.slug) - first_need.get(p.id, 0.0)) for p in (milk, curd)
    }
    observed = await service.pantry_observations(db_session, profile, catalog, SECOND)
    assert set(observed) == {milk.id, curd.id}  # perishable daily use only: no atta, no sugar
    assert observed[milk.id] == [(pytest.approx(shares[milk.id]), "ran_out")]
    assert observed[curd.id] == [(pytest.approx(shares[curd.id]), "wasted")]

    second = await cycle_on(db_session, SECOND)
    lines = {line.product_id: line for line in await service.lines_of(db_session, second.id)}
    second_need, _ = planning.recipe_need(profile, catalog, planning.plan_of(second.meal_plan))
    for product, tap in ((milk, "ran_out"), (curd, "wasted")):
        line = lines[product.id]
        step = product.step_qty * planning.BASE_FACTOR[product.step_unit]
        mean = expected_mean(profile, product.slug, step, shares[product.id], tap)
        assert line.reason_code == "pantry_low" and line.source == "ai"
        assert line.reason_params is not None
        assert line.reason_params["weeks"] == 1 and line.reason_params["source"] == "fallback"
        assert line.reason_params["mean"] == mean
        assert line.reason_params["p10"] <= mean <= line.reason_params["p90"]
        # The twin's daily use, with this week's recipes on top, in steps: no compounding.
        assert line.qty == rules.steps_needed(
            mean + second_need.get(product.id, 0.0),
            product.step_qty,
            planning.BASE_FACTOR[product.step_unit],
        )
    # Staples (atta, sugar, oil, salt) stay on the rule table (and its carry-over).
    assert {p for p, line in lines.items() if line.reason_code == "pantry_low"} == {
        milk.id,
        curd.id,
    }
    kept = await pantry_predictions(db_session)
    assert len(kept) == 1 and kept[0].for_date == SECOND


@time_machine.travel(NOW, tick=False)
async def test_the_twin_is_off_by_default(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    first = await first_week(api, google, db_session, then_generate=True)
    second = await cycle_on(db_session, SECOND)
    lines = {line.product_id: line for line in await service.lines_of(db_session, second.id)}
    milk = next(line for line in first["lines"] if line["slug"] == "milk")
    assert lines[uuid.UUID(milk["product_id"])].reason_code == "daily_use"
    assert lines[uuid.UUID(milk["product_id"])].source == "rule"
    assert await pantry_predictions(db_session) == []


@time_machine.travel(NOW, tick=False)
async def test_the_ml_service_answers_the_twin(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients(pantry_twin_enabled=True)
    first = await first_week(api, google, db_session, then_generate=False)
    household = uuid.UUID(first["household_id"])
    profile, catalog = await world_of(db_session, household)
    asked: list[dict[str, Any]] = []

    def ml(request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/pantry/quantities":
            return httpx2.Response(503)  # the planner falls back; only the twin answers
        asked.append(json.loads(request.content))
        answer = ml_pantry.quantities(MlPantryRequest.model_validate(asked[-1]))
        return httpx2.Response(200, json=answer.model_dump(mode="json"))

    settings = make_settings(
        ml_service_url="http://ml.test", ml_service_token="t", pantry_twin_enabled=True
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(ml)) as http:
        box, plan_source, _ = await service.draft(
            db_session, http, settings, profile, catalog, SECOND, record_for=household
        )

    assert plan_source == "fallback"
    assert sorted(item["product"] for item in asked[0]["items"]) == ["curd", "milk"]
    milk = catalog.product_by_slug("milk")
    assert milk is not None
    line = next(line for line in box.lines if line.product_id == milk.id)
    assert line.reason_code == "pantry_low" and line.reason_params is not None
    assert line.reason_params["source"] == "ml"
    kept = await pantry_predictions(db_session)
    assert (
        len(kept) == 1
        and kept[0].source == "ml"
        and kept[0].model_name == ("pantry-gamma-poisson-v0")
    )


@time_machine.travel(NOW, tick=False)
async def test_learned_cohort_priors_replace_the_rule_table_in_the_twin(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """v1 (M10 §3.3): once the refit job stored priors for the household's cohort, the twin
    starts from them instead of the family-size table and says so in the line's params."""
    from gharbasket.modules.identity import api as identity_api
    from gharbasket.modules.subscriptions import rules
    from gharbasket.modules.subscriptions.rules import Member

    api = clients(pantry_twin_enabled=True)  # the twin is off by default (PR #118)
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
