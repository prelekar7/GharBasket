"""`GET /v1/analytics/cohorts` and `/v1/analytics/partner-earnings` (M12 §3.5) on known fixtures:
retention numbers per signup week, a distribution of partner earnings with percentiles and the
Gini, the 5-partner suppression, data sources never pooled, role scoping, empty ranges and the
validation errors. Also that every research metric of §3.5's table has its live counterpart."""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import cohorts, earnings, kpis, refresh
from gharbasket.modules.analytics.models import FactHousehold, RefreshRun

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_kpis import (
    HUB,
    NEHA,
    NOW,
    OTHER_HUB,
    PERSONAL,
    PRIYA,
    SANJAY,
    SOCIETY,
    VIKRAM,
    WEEK1,
    WEEK2,
    earning,
    order,
    seed_facts,
    signed_in,
    walk_keys,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

IST = ZoneInfo("Asia/Kolkata")
OCT5, OCT12, OCT26 = date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 26)  # Mondays
NIGHTLY_RAN = NOW - timedelta(hours=1)


@pytest.fixture(autouse=True)
def clock() -> Iterator[None]:
    with time_machine.travel(NOW, tick=False):  # Tuesday 3 Nov 2026: week 2 Nov has not ended
        yield


def household(n: int, cohort_week: date, **values: Any) -> FactHousehold:
    return FactHousehold(
        **{
            "household_id": uuid.UUID(int=0x1000 + n),
            "hub_id": HUB,
            "society_id": SOCIETY,
            "created_on": cohort_week + timedelta(days=2),
            "cohort_week": cohort_week,
            "status": "active",
            "data_source": "real",
        }
        | values
    )


def delivers(n: int, cohort_week: date, offset: int, **values: Any) -> Any:
    """A delivered order of household `n` in the ISO week `offset` after its signup week."""
    day = cohort_week + timedelta(weeks=offset, days=1)
    return order(day, household_id=uuid.UUID(int=0x1000 + n), **values)


async def seed_cohorts(session: AsyncSession) -> None:
    """Three real cohorts and a synthetic one, on the Tuesday 3 Nov of the clock.

    - 5 Oct, the seed hub: 10 households. Active in the week after: 9 (one of them twice); two
      weeks after: 8; three weeks after: 5 (one a non-delivered order only, which counts as no
      one). A delivery in the signup week itself counts for no offset.
    - 5 Oct, the other hub: 2 households, one active in week 1.
    - 12 Oct, the other hub: 5 households (not small), 3 then 5 active.
    - 26 Oct, the seed hub: 4 households (small); its week 1 is still in progress.
    - 5 Oct, synthetic: 2 households, one active in week 1.
    """
    session.add_all(
        [
            *(household(n, OCT5) for n in range(1, 11)),
            *(household(n, OCT5, hub_id=OTHER_HUB) for n in (11, 12)),
            *(household(n, OCT12, hub_id=OTHER_HUB) for n in range(13, 18)),
            *(household(n, OCT26) for n in range(18, 22)),
            *(household(n, OCT5, data_source="synthetic") for n in (30, 31)),
        ]
    )
    orders = [
        *(delivers(n, OCT5, 1) for n in range(1, 10)),
        delivers(1, OCT5, 1, total_paise=1),  # the same household again: still one
        *(delivers(n, OCT5, 2) for n in range(1, 9)),
        *(delivers(n, OCT5, 3) for n in range(1, 6)),
        delivers(9, OCT5, 3, status="undelivered"),
        delivers(10, OCT5, 0),
        delivers(11, OCT5, 1, hub_id=OTHER_HUB),
        *(delivers(n, OCT12, 1, hub_id=OTHER_HUB) for n in (13, 14, 15)),
        *(delivers(n, OCT12, 2, hub_id=OTHER_HUB) for n in range(13, 18)),
        delivers(18, OCT26, 1),
        delivers(30, OCT5, 1, data_source="synthetic"),
    ]
    session.add_all(orders)
    await session.flush()
    await refresh.refresh_views(session, refresh.VIEWS)


def ran_nightly(session: AsyncSession, finished: datetime = NIGHTLY_RAN) -> None:
    session.add(
        RefreshRun(
            job=refresh.NIGHTLY,
            scheduled_for=finished - timedelta(minutes=5),
            window_from=finished.date() - timedelta(days=35),
            window_to=finished.date(),
            status="ok",
            started_at=finished - timedelta(minutes=5),
            finished_at=finished,
        )
    )


async def get_cohorts(
    api: httpx2.AsyncClient, auth: dict[str, str], **params: Any
) -> httpx2.Response:
    return await api.get(
        "/v1/analytics/cohorts", params={"data_source": "real", **params}, headers=auth
    )


async def get_earnings(
    api: httpx2.AsyncClient, auth: dict[str, str], **params: Any
) -> httpx2.Response:
    return await api.get(
        "/v1/analytics/partner-earnings", params={"data_source": "real", **params}, headers=auth
    )


# --- cohorts --------------------------------------------------------------------------------


async def test_retention_per_signup_week_on_a_known_fixture(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_cohorts(db_session)
    ran_nightly(db_session)
    await db_session.flush()
    api, admin = await signed_in(clients, google, NEHA)

    answer = await get_cohorts(api, admin, hub_id=str(HUB), weeks=12)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert answer.headers["Cache-Control"] == "private, max-age=60"
    assert (body["data_source"], body["hub_id"], body["weeks"]) == ("real", str(HUB), 12)
    assert body["stale"] is False and body["as_of"] is not None
    assert [c["week"] for c in body["cohorts"]] == ["2026-10-05", "2026-10-26"]
    first, second = body["cohorts"]
    # Weeks 1-3 after 5 Oct have ended (the third on 1 Nov); the 4th is this week
    assert (first["households"], first["small"]) == (10, False)
    assert first["retention_bp"] == [9_000, 8_000, 5_000] + [None] * 9
    assert (second["households"], second["small"]) == (4, True)
    assert second["retention_bp"] == [None] * 12  # its first week is in progress
    assert all(len(c["retention_bp"]) == 12 for c in body["cohorts"])


async def test_every_hub_adds_up_the_cohorts_of_the_same_week(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_cohorts(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    answer = await get_cohorts(api, admin, weeks=12)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["hub_id"] is None
    assert (body["stale"], body["as_of"]) == (True, None)  # no nightly run has covered it
    by_week = {c["week"]: c for c in body["cohorts"]}
    assert list(by_week) == ["2026-10-05", "2026-10-12", "2026-10-26"]  # oldest first
    # 5 Oct across both hubs: 12 households; 10 then 8 then 5 of them active
    assert by_week["2026-10-05"]["households"] == 12
    assert by_week["2026-10-05"]["retention_bp"][:4] == [8_333, 6_667, 4_167, None]
    # 12 Oct: 5 households, not small; 3 then 5 active, then the week of 2 Nov (not ended)
    assert by_week["2026-10-12"]["small"] is False
    assert by_week["2026-10-12"]["retention_bp"][:3] == [6_000, 10_000, None]

    other = await get_cohorts(api, admin, hub_id=str(OTHER_HUB), weeks=12)
    assert [c["households"] for c in other.json()["cohorts"]] == [2, 5]
    assert other.json()["cohorts"][0]["retention_bp"][:2] == [5_000, 0]


async def test_weeks_choose_the_signup_weeks_and_the_retention_length(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_cohorts(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    four = (await get_cohorts(api, admin, hub_id=str(HUB), weeks=4)).json()
    # The last 4 signup weeks start 12 Oct: 5 Oct falls out, and retention runs 4 weeks
    assert [c["week"] for c in four["cohorts"]] == ["2026-10-26"]
    assert four["cohorts"][0]["retention_bp"] == [None] * 4
    widest = (await get_cohorts(api, admin, hub_id=str(HUB), weeks=26)).json()
    assert len(widest["cohorts"][0]["retention_bp"]) == 26
    default = (await get_cohorts(api, admin, hub_id=str(HUB))).json()
    assert default["weeks"] == 12 and len(default["cohorts"][0]["retention_bp"]) == 12


async def test_a_week_is_reached_once_it_has_ended() -> None:
    sunday, monday = date(2026, 11, 1), date(2026, 11, 2)
    assert cohorts.reached(OCT26, 1, date(2026, 11, 9)) is True  # 2-8 Nov ended on Sunday
    assert cohorts.reached(OCT26, 1, date(2026, 11, 8)) is False  # the Sunday itself is not over
    assert cohorts.reached(OCT5, 3, monday) is True
    assert cohorts.reached(OCT5, 3, sunday) is False


async def test_data_sources_are_never_pooled(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_cohorts(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    synthetic = await get_cohorts(api, admin, data_source="synthetic", weeks=12)

    assert synthetic.status_code == 200, synthetic.text
    body = synthetic.json()
    assert body["data_source"] == "synthetic"
    assert len(body["cohorts"]) == 1
    only = body["cohorts"][0]
    assert (only["week"], only["households"], only["small"]) == ("2026-10-05", 2, True)
    assert only["retention_bp"][:4] == [5_000, 0, 0, None]  # none of the real orders count


async def test_no_cohorts_without_households(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, admin = await signed_in(clients, google, NEHA)

    empty = await get_cohorts(api, admin, weeks=12)

    assert empty.status_code == 200, empty.text
    assert empty.json()["cohorts"] == []
    await seed_cohorts(db_session)
    elsewhere = await get_cohorts(api, admin, hub_id=str(uuid.UUID(int=0x99)), weeks=12)
    assert elsewhere.status_code == 200 and elsewhere.json()["cohorts"] == []


async def test_only_an_admin_reads_cohorts(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_cohorts(db_session)
    for phone in (VIKRAM, SANJAY, PRIYA):  # hub manager, franchise owner, customer
        api, auth = await signed_in(clients, google, phone)
        refused = await get_cohorts(api, auth, hub_id=str(HUB))
        assert refused.status_code == 403, phone
    anonymous = await api.get("/v1/analytics/cohorts", params={"data_source": "real"})
    assert anonymous.status_code == 401


@pytest.mark.parametrize(
    ("params", "status", "code"),
    [
        ({"weeks": 26}, 200, None),
        ({"weeks": 4}, 200, None),
        ({"weeks": 27}, 422, "analytics.range_too_long"),
        ({"weeks": 3}, 422, "analytics.range_invalid"),
        ({"weeks": 0}, 422, "analytics.range_invalid"),
        ({"weeks": "many"}, 422, None),
        ({"data_source": "pilot"}, 422, None),
        ({"hub_id": "not-a-uuid"}, 422, None),
    ],
)
async def test_validation(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    params: dict[str, Any],
    status: int,
    code: str | None,
) -> None:
    api, admin = await signed_in(clients, google, NEHA)

    answer = await get_cohorts(api, admin, **params)

    assert answer.status_code == status, answer.text
    if code:
        assert answer.json()["code"] == code


async def test_a_data_source_is_required(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, admin = await signed_in(clients, google, NEHA)

    answer = await api.get("/v1/analytics/cohorts", params={"weeks": 12}, headers=admin)

    assert answer.status_code == 422
    assert answer.json()["code"] == "analytics.data_source_required"


# --- partner earnings -----------------------------------------------------------------------


async def test_the_weekly_distribution_with_percentiles_and_the_gini(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)  # 5 kitchens in week 1 (10-50k), 4 in week 2, a rider each week
    ran_nightly(db_session)
    await db_session.flush()
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await get_earnings(
        api, manager, hub_id=str(HUB), **{"from": "2026-10-26", "to": "2026-11-08"}
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert answer.headers["Cache-Control"] == "private, max-age=60"
    assert (body["hub_id"], body["kind"], body["data_source"]) == (str(HUB), "kitchen", "real")
    assert body["stale"] is False and body["as_of"] is not None
    assert body["weeks"] == [
        {
            "week": "2026-10-26",
            "partners": 5,
            "total_paise": 150_000,
            "p10_paise": 14_000,  # rank 0.4 of 10k..50k
            "median_paise": 30_000,
            "p90_paise": 46_000,  # rank 3.6
            "gini_bp": 2_667,
        },
        # Four kitchens: the total only. Percentiles and the Gini could point at one person
        {
            "week": "2026-11-02",
            "partners": 4,
            "total_paise": 100_000,
            "p10_paise": None,
            "median_paise": None,
            "p90_paise": None,
            "gini_bp": None,
        },
    ]
    assert not (PERSONAL | {"partner_id", "partners_id"}) & set(walk_keys(body))


async def test_kinds_are_kept_apart(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, owner = await signed_in(clients, google, SANJAY)
    window = {"hub_id": str(HUB), "from": "2026-10-26", "to": "2026-11-08"}

    riders = await get_earnings(api, owner, kind="rider", **window)

    assert riders.status_code == 200, riders.text
    weeks = riders.json()["weeks"]
    assert [(w["partners"], w["total_paise"]) for w in weeks] == [(1, 8_000), (1, 8_000)]
    assert all(w["median_paise"] is None for w in weeks)  # one rider: no distribution at all
    captains = await get_earnings(api, owner, kind="captain", **window)
    assert [w["partners"] for w in captains.json()["weeks"]] == [0, 0]


async def test_a_partner_is_paid_when_their_week_adds_up_above_zero(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    week = date(2026, 10, 19)
    db_session.add_all(
        [
            *(earning(i, week, "kitchen", 10_000 * i) for i in range(1, 6)),
            earning(1, week, "kitchen", 5_000, basis="tap"),  # a second basis adds to partner 1
            earning(6, week, "kitchen", 0),  # accrued nothing: not paid, not a partner
        ]
    )
    await db_session.flush()
    await refresh.refresh_views(db_session, refresh.VIEWS)
    api, admin = await signed_in(clients, google, NEHA)

    answer = await get_earnings(
        api, admin, hub_id=str(HUB), **{"from": "2026-10-19", "to": "2026-10-19"}
    )

    row = answer.json()["weeks"][0]
    # 15k, 20k, 30k, 40k, 50k
    assert (row["partners"], row["total_paise"]) == (5, 155_000)
    assert (row["p10_paise"], row["median_paise"], row["p90_paise"]) == (17_000, 30_000, 46_000)


async def test_every_hub_is_one_distribution_not_an_average_of_hubs(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, admin = await signed_in(clients, google, NEHA)
    window = {"from": "2026-10-26", "to": "2026-11-08"}

    one = (await get_earnings(api, admin, hub_id=str(HUB), **window)).json()
    every = (await get_earnings(api, admin, **window)).json()

    assert one["hub_id"] == str(HUB) and every["hub_id"] is None
    assert every["weeks"] == one["weeks"]  # the fact route gives the view's numbers

    db_session.add_all(
        earning(i, WEEK1, "kitchen", paise, hub_id=OTHER_HUB)
        for i, paise in ((20, 5_000), (21, 15_000), (22, 25_000))
    )
    await db_session.flush()
    await refresh.refresh_views(db_session, refresh.VIEWS)
    pooled = (await get_earnings(api, admin, **window)).json()["weeks"][0]
    values = [5_000, 10_000, 15_000, 20_000, 25_000, 30_000, 40_000, 50_000]
    assert pooled["partners"] == 8 and pooled["total_paise"] == sum(values) == 195_000
    assert (pooled["p10_paise"], pooled["median_paise"], pooled["p90_paise"]) == (
        8_500,
        22_500,
        43_000,
    )
    assert pooled["gini_bp"] == kpis.gini_bp(values)
    other = (await get_earnings(api, admin, hub_id=str(OTHER_HUB), **window)).json()["weeks"][0]
    assert (other["partners"], other["median_paise"], other["gini_bp"]) == (3, None, None)


async def test_data_sources_are_never_pooled_in_earnings(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)  # one synthetic kitchen paid 1 paisa in week 2
    api, admin = await signed_in(clients, google, NEHA)
    window = {"from": "2026-11-02", "to": "2026-11-08"}

    synthetic = await get_earnings(api, admin, hub_id=str(HUB), data_source="synthetic", **window)
    real = await get_earnings(api, admin, hub_id=str(HUB), **window)

    assert synthetic.json()["weeks"][0]["partners"] == 1
    assert synthetic.json()["weeks"][0]["total_paise"] == 1
    assert real.json()["weeks"][0]["total_paise"] == 100_000
    pooled = await get_earnings(api, admin, data_source="synthetic", **window)
    assert pooled.json()["weeks"][0]["total_paise"] == 1


async def test_empty_ranges_list_every_week_with_nobody_paid(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await get_earnings(
        api, manager, hub_id=str(HUB), **{"from": "2025-01-08", "to": "2025-01-21"}
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["weeks"] == [
        {
            "week": week,
            "partners": 0,
            "total_paise": 0,
            "p10_paise": None,
            "median_paise": None,
            "p90_paise": None,
            "gini_bp": None,
        }
        for week in ("2025-01-06", "2025-01-13", "2025-01-20")  # whole ISO weeks
    ]
    nobody = await get_earnings(api, manager, hub_id=str(HUB))  # the default: the last 12 weeks
    assert len(nobody.json()["weeks"]) == 12
    assert nobody.json()["weeks"][-1]["week"] == "2026-11-02"
    assert nobody.json()["weeks"][-2]["partners"] == 5  # and week 1 of the fixture is there


async def test_earnings_follow_the_hub_scope(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    own = await get_earnings(api, manager, hub_id=str(HUB))
    assert own.status_code == 200, own.text
    elsewhere = await get_earnings(api, manager, hub_id=str(OTHER_HUB))
    assert elsewhere.status_code == 403 and elsewhere.json()["code"] == "auth.forbidden"
    left_out = await get_earnings(api, manager)  # a non-admin must name the hub
    assert left_out.status_code == 403 and left_out.json()["code"] == "auth.forbidden"
    chosen = await get_earnings(
        api, {**manager, "X-Active-Role": f"hub_manager:{HUB}"}, hub_id=str(HUB)
    )
    assert chosen.status_code == 200, chosen.text

    owner_api, owner = await signed_in(clients, google, SANJAY)
    assert (await get_earnings(owner_api, owner, hub_id=str(HUB))).status_code == 200
    assert (await get_earnings(owner_api, owner, hub_id=str(OTHER_HUB))).status_code == 403
    customer_api, customer = await signed_in(clients, google, PRIYA)
    assert (await get_earnings(customer_api, customer, hub_id=str(HUB))).status_code == 403
    anonymous = await api.get("/v1/analytics/partner-earnings", params={"data_source": "real"})
    assert anonymous.status_code == 401

    admin_api, admin = await signed_in(clients, google, NEHA)
    every = await get_earnings(admin_api, admin)
    assert every.status_code == 200 and every.json()["hub_id"] is None


@pytest.mark.parametrize(
    ("params", "status", "code"),
    [
        ({"kind": "founder"}, 422, None),
        ({"data_source": "pilot"}, 422, None),
        ({"from": "2026-11-03", "to": "2026-11-02"}, 422, "analytics.range_invalid"),
        ({"from": "2024-01-01", "to": "2026-11-03"}, 422, "analytics.range_too_long"),
        ({"from": "2024-11-11", "to": "2026-11-03"}, 200, None),  # exactly 104 weeks
        ({"from": "2026-13-45"}, 422, None),
    ],
)
async def test_earnings_validation(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    params: dict[str, Any],
    status: int,
    code: str | None,
) -> None:
    api, admin = await signed_in(clients, google, NEHA)

    answer = await get_earnings(api, admin, hub_id=str(HUB), **params)

    assert answer.status_code == status, answer.text
    if code:
        assert answer.json()["code"] == code
    if status == 200:
        assert len(answer.json()["weeks"]) == 104


async def test_earnings_need_a_data_source(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, admin = await signed_in(clients, google, NEHA)

    answer = await api.get(
        "/v1/analytics/partner-earnings", params={"hub_id": str(HUB)}, headers=admin
    )

    assert answer.status_code == 422
    assert answer.json()["code"] == "analytics.data_source_required"


async def test_percentiles_interpolate_and_round_half_up() -> None:
    tenth, half, ninth = Fraction(1, 10), Fraction(1, 2), Fraction(9, 10)
    assert earnings.percentile_paise([10, 20, 30, 40, 50], half) == 30
    assert earnings.percentile_paise([10, 20, 30, 40, 50], tenth) == 14
    assert earnings.percentile_paise([10, 20, 30, 40, 50], ninth) == 46
    assert earnings.percentile_paise([1, 2], half) == 2  # 1.5 rounds up
    assert earnings.percentile_paise([7], ninth) == 7
    row = earnings.week_row(date(2026, 11, 2), [3, 1, 2, 4])
    assert (row.partners, row.total_paise, row.median_paise, row.gini_bp) == (4, 10, None, None)


# --- the research metrics (§3.5) -------------------------------------------------------------

# Eval protocol metric -> the live counterparts of §3.5's table, in `/v1/analytics/kpis`.
RESEARCH_METRICS = {
    "M-A pantry twin": ("pantry_misses_bp",),
    "M-B ranker": ("edits_per_100_boxes",),
    "M-C planner": ("weeks_accepted_bp", "accepted_ml_bp", "accepted_fallback_bp"),
    "M-E forecast": ("waste_g_per_100kg", "shortage_lines_bp"),
    "M-F routing": ("delivery_cost_per_order_paise", "km_per_order_m", "on_time_bp"),
    "M-G kitchens": ("kitchen_gini_bp",),
}


async def test_every_research_metric_has_its_live_counterpart(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, admin = await signed_in(clients, google, NEHA)
    window = {"from": "2026-10-26", "to": "2026-11-08"}

    answer = await api.get(
        "/v1/analytics/kpis",
        params={"hub_id": str(HUB), "data_source": "real", **window},
        headers=admin,
    )

    body = answer.json()
    for metric, keys in RESEARCH_METRICS.items():
        for key in keys:
            assert key in body["series"] and key in body["totals"], (metric, key)
    # M-G's two live forms agree: the KPI series and the partner-earnings endpoint
    spread = (await get_earnings(api, admin, hub_id=str(HUB), **window)).json()["weeks"]
    assert [w["gini_bp"] for w in spread] == body["series"]["kitchen_gini_bp"] == [2_667, None]
    assert (WEEK1.isoformat(), WEEK2.isoformat()) == tuple(body["periods"])
