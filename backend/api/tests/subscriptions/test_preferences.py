"""Planner v2 (M10 §3.2): the ranker's scores become a liking per dish, the plan request carries
it, and a household with two approved weeks gets its candidates ranked (the fallback here)."""

import uuid
from datetime import date, timedelta

import httpx2
import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.subscriptions import planning, service
from gharbasket.tools.seed import SEED_HUB_ID

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import NOW, PRIYA, as_user, subscribe

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]


async def test_scores_become_a_rank_scaled_liking_per_dish(db_session: AsyncSession) -> None:
    catalog = await catalog_api.plan_catalog(db_session, SEED_HUB_ID)
    mains = [r for r in catalog.recipes if not r.is_side]
    first, second = mains[0], mains[1]
    scores = {i.product_id: 0.9 for i in first.ingredients}
    for i in second.ingredients:
        scores.setdefault(i.product_id, 0.1)

    liking = planning.recipe_preferences(scores, catalog)

    assert liking[first.slug] == 1.0 and 0.0 <= liking[second.slug] <= 1.0
    assert all(0.0 <= v <= 1.0 for v in liking.values())
    assert set(liking) <= {r.slug for r in mains}
    assert planning.recipe_preferences({}, catalog) == {}
    one = planning.recipe_preferences({first.ingredients[0].product_id: 0.3}, catalog)
    assert all(v == 1.0 for v in one.values())


@time_machine.travel(NOW, tick=False)
async def test_two_approved_weeks_rank_the_household_and_tilt_the_plan(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, user_id = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    household = await identity_api.household_for_user(db_session, user_id)
    assert household is not None
    removed: str | None = None
    approved_ids: list[uuid.UUID] = []
    for week, (scheduled_for, delivery) in enumerate(
        (
            ("2026-10-10T12:30:00Z", date(2026, 10, 13)),
            ("2026-10-17T12:30:00Z", date(2026, 10, 20)),
        )
    ):
        ran = await api.post(
            "/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": scheduled_for}
        )
        assert ran.status_code == 200, ran.text
        cycle = await service.cycle_for(db_session, household.id, delivery)
        assert cycle is not None
        version = cycle.version
        if week == 1:  # Priya removes a line in week 2: the ranker's removed_last next week
            kept = {line.product_id for line in await service.lines_of(db_session, approved_ids[0])}
            lines = await service.lines_of(db_session, cycle.id)
            removed = str(next(line.product_id for line in lines if line.product_id in kept))
            edited = await api.patch(
                f"/v1/cycles/{cycle.id}/lines",
                json={"ops": [{"op": "remove", "product_id": removed}]},
                headers={**priya, "If-Match": f'"{version}"'},
            )
            assert edited.status_code == 200, edited.text
            version = edited.json()["version"]
        approved = await api.post(
            f"/v1/cycles/{cycle.id}/approve",
            headers={
                **priya,
                "Idempotency-Key": str(uuid.uuid4()),
                "If-Match": f'"{version}"',
            },
        )
        assert approved.status_code == 200, approved.text
        approved_ids.append(cycle.id)
    catalog = await catalog_api.plan_catalog(db_session, SEED_HUB_ID)
    next_delivery = date(2026, 10, 27)

    candidates = await service.ranker_candidates(
        db_session, household.id, SEED_HUB_ID, next_delivery, catalog
    )
    assert candidates and all(0 < c.frequency <= 1 for c in candidates)
    assert all(1 <= c.weeks_since <= 2 and c.price_paise > 0 for c in candidates)
    # Priya is the hub's only household with a box last week: popularity is 1 or 0.
    assert {c.popularity for c in candidates} <= {0.0, 1.0} and any(
        c.popularity for c in candidates
    )
    by_product = {c.product: c for c in candidates}
    gone = next(p for p in catalog.products.values() if str(p.id) == removed)
    assert by_product[gone.slug].removed_last and by_product[gone.slug].popularity == 0.0
    assert by_product[gone.slug].weeks_since == 2 and by_product[gone.slug].frequency == 0.5
    assert sum(c.removed_last for c in candidates) == 1
    # One week of history is not enough.
    assert (
        await service.ranker_candidates(
            db_session, household.id, SEED_HUB_ID, date(2026, 10, 20), catalog
        )
        == []
    )

    profile = await identity_api.household_plan_profile(db_session, household.id)
    assert profile is not None
    async with httpx2.AsyncClient() as http:  # no ML service: the frequency fallback ranks
        liking = await service.learned_preferences(
            db_session, http, make_settings(), profile, catalog, next_delivery, record_for=None
        )
    assert liking and all(0.0 <= v <= 1.0 for v in liking.values())
    request = planning.plan_request(
        profile, catalog, next_delivery - timedelta(days=1), [], None, preferences=liking
    )
    assert any(r["preference"] is not None for r in request["recipes"])
    assert all(r["preference"] is None for r in request["recipes"] if r["is_side"])

    # The customer's preview waits on one ML call at most: the planner's, never the ranker's.
    called: list[str] = []

    def down(request: httpx2.Request) -> httpx2.Response:
        called.append(request.url.path)
        return httpx2.Response(503)

    settings = make_settings(ml_service_url="http://ml.test", ml_service_token="t")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(down)) as http:
        await service.draft(
            db_session,
            http,
            settings,
            profile,
            catalog,
            next_delivery,
            record_for=None,
            preview=True,
        )
        assert called == ["/plan/week"]
        called.clear()
        await service.draft(
            db_session, http, settings, profile, catalog, next_delivery, record_for=None
        )
        assert "/basket/rank" in called
