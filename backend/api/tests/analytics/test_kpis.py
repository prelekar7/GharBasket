"""`GET /v1/analytics/kpis` and `/v1/analytics/societies` (M12 §3.3) on a small seeded fact set:
rates in basis points recomputed from their parts, totals over the range, nulls where nothing is
defined, data sources never pooled, day against week grain, the range limits, the hub scope, and
no personal field anywhere."""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
import time_machine
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gharbasket.modules.analytics import freshness, kpis, reads, refresh, router
from gharbasket.modules.analytics.models import (
    FactCommunity,
    FactCycle,
    FactHousehold,
    FactHubDay,
    FactIssue,
    FactOrder,
    FactPartnerWeek,
    FactRoute,
    RefreshRun,
)
from gharbasket.modules.community import api as community_api
from gharbasket.modules.subscriptions.models import Plan, Subscription
from gharbasket.platform.db import asyncpg_url
from gharbasket.platform.errors import ProblemError
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=IST)  # Tuesday
WEEK1, WEEK2 = date(2026, 10, 26), date(2026, 11, 2)  # Mondays
TUESDAY1, TUESDAY2 = date(2026, 10, 27), date(2026, 11, 3)
HUB, OTHER_HUB = SEED_HUB_ID, uuid.UUID(int=0x77)
SOCIETY = SEED_SOCIETY_ID
HH1, HH2, HH3, HH9 = (uuid.UUID(int=i) for i in (1, 2, 3, 9))

NEHA = "+919800001011"  # admin
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub
PRIYA = "+919800001001"  # customer

PERSONAL = {
    "household_id",
    "user_id",
    "phone",
    "flat",
    "address",
    "email",
    "tower",
    "rider",
    "packer",
    "captain",
    "display_name",
}


@pytest.fixture(autouse=True)
def clock() -> Iterator[None]:
    with time_machine.travel(NOW, tick=False):
        yield


def _make[T](model: type[T], defaults: dict[str, Any], values: dict[str, Any]) -> T:
    return model(**(defaults | values))


def order(delivery: date, **values: Any) -> FactOrder:
    return _make(
        FactOrder,
        {
            "order_id": uuid.uuid4(),
            "household_id": HH1,
            "hub_id": HUB,
            "society_id": SOCIETY,
            "kind": "box",
            "delivery_date": delivery,
            "status": "delivered",
            "total_paise": 60_000,
            "fees_paise": 0,
            "refunded_paise": 0,
            "credited_paise": 0,
            "lines": 10,
            "short_lines": 0,
            "on_time": True,
            "first_attempt": True,
            "data_source": "real",
        },
        values,
    )


def cycle(delivery: date, **values: Any) -> FactCycle:
    return _make(
        FactCycle,
        {
            "cycle_id": uuid.uuid4(),
            "household_id": uuid.uuid4(),
            "hub_id": HUB,
            "society_id": SOCIETY,
            "week_start": monday(delivery),
            "delivery_date": delivery,
            "status": "locked",
            "auto_approved": False,
            "approved_before_cutoff": True,
            "edits": 0,
            "plan_source": "ml",
            "ran_out_taps": 0,
            "wasted_taps": 0,
            "just_right_taps": 0,
            "data_source": "real",
        },
        values,
    )


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def issue(day: date, **values: Any) -> FactIssue:
    at = datetime.combine(day, datetime.min.time().replace(hour=10), IST)
    return _make(
        FactIssue,
        {
            "issue_id": uuid.uuid4(),
            "order_id": uuid.uuid4(),
            "hub_id": HUB,
            "kind": "missing",
            "status": "credited",
            "amount_paise": 5_000,
            "credit_paise": 5_000,
            "resolved_by": "auto",
            "created_at": at,
            "resolved_at": at,
            "data_source": "real",
        },
        values,
    )


def route(day: date, **values: Any) -> FactRoute:
    return _make(
        FactRoute,
        {
            "route_id": uuid.uuid4(),
            "hub_id": HUB,
            "on_date": day,
            "kind": "box",
            "km_m": 0,
            "cost_paise": 0,
            "orders": 0,
            "stops": 0,
            "on_time_stops": 0,
            "data_source": "real",
        },
        values,
    )


def earning(partner: int, week: date, kind: str, paise: int, **values: Any) -> FactPartnerWeek:
    return _make(
        FactPartnerWeek,
        {
            "partner_id": uuid.UUID(int=0x100 + partner),
            "iso_week": week,
            "basis": "bag",
            "hub_id": HUB,
            "kind": kind,
            "units": 1,
            "amount_paise": paise,
            "data_source": "real",
        },
        values,
    )


def community(kind: str, day: date, **values: Any) -> FactCommunity:
    return _make(
        FactCommunity,
        {
            "source": "donation",
            "source_id": uuid.uuid4(),
            "hub_id": HUB,
            "society_id": SOCIETY,
            "on_date": day,
            "kind": kind,
            "items": 0,
            "kg_tenths": 0,
            "meals": 0,
            "points": 0,
            "data_source": "real",
        },
        values,
    )


async def seed_facts(session: AsyncSession, *, ran_at: datetime | None = NOW) -> None:
    """Two weeks of the seed hub's facts, hub 2's one order, and the synthetic ones beside them."""
    session.add_all(
        [
            # Week 1 (Tue 27 Oct): 3 delivered (2 boxes, 1 add-on) for 2 households
            order(TUESDAY1, household_id=HH1, short_lines=1),
            order(
                TUESDAY1,
                household_id=HH2,
                total_paise=62_000,
                refunded_paise=2_000,
                on_time=False,
                first_attempt=False,
            ),
            order(TUESDAY1, household_id=HH1, kind="addon", total_paise=5_000, lines=2),
            # Week 2 (Tue 3 Nov): one delivered box, one box not delivered
            order(TUESDAY2, household_id=HH1, total_paise=64_000, lines=12, short_lines=2),
            order(
                TUESDAY2,
                household_id=HH3,
                status="undelivered",
                total_paise=50_000,
                on_time=None,
                first_attempt=False,
            ),
            # Another hub, and the synthetic world: never part of this hub's real numbers
            order(TUESDAY1, household_id=HH9, hub_id=OTHER_HUB, total_paise=70_000),
            order(TUESDAY2, household_id=HH2, total_paise=99_999, data_source="synthetic"),
        ]
    )
    session.add_all(
        [
            cycle(TUESDAY1, edits=1, ran_out_taps=1, wasted_taps=1, just_right_taps=2),
            cycle(TUESDAY1, status="approved", auto_approved=True),
            cycle(TUESDAY1, status="skipped", plan_source="fallback", approved_before_cutoff=False),
            cycle(
                TUESDAY1,
                status="awaiting_approval",
                plan_source="fallback",
                approved_before_cutoff=False,
            ),
            cycle(TUESDAY2, edits=3, just_right_taps=1),
            cycle(TUESDAY2, data_source="synthetic"),
        ]
    )
    session.add_all(
        [
            issue(TUESDAY1),
            issue(TUESDAY1, status="open", credit_paise=0, resolved_by=None, resolved_at=None),
            route(TUESDAY1, km_m=12_000, cost_paise=30_000, orders=3),
            route(TUESDAY1, kind="tiffin", km_m=900, cost_paise=1_000, orders=1),  # not a box route
            route(TUESDAY2, km_m=8_000, cost_paise=25_000, orders=2),
            route(TUESDAY2, hub_id=OTHER_HUB, km_m=1, cost_paise=None, orders=1),
            FactHubDay(
                hub_id=HUB,
                on_date=TUESDAY1,
                packed_g=100_000,
                waste_ngo_g=300,  # rescued, not waste (M6 §3.5)
                waste_bin_g=500,
                waste_kitchen_g=200,  # rescued, not waste
                waste_imperfect_g=0,
                surplus_offered_g=0,
                surplus_taken_g=0,
                data_source="real",
            ),
            FactHubDay(
                hub_id=HUB,
                on_date=TUESDAY2,
                packed_g=50_000,
                waste_ngo_g=1_000,
                waste_bin_g=0,
                waste_kitchen_g=0,
                waste_imperfect_g=0,
                surplus_offered_g=0,
                surplus_taken_g=0,
                data_source="real",
            ),
        ]
    )
    session.add_all(
        [
            *(earning(i, WEEK1, "kitchen", 10_000 * i) for i in range(1, 6)),
            earning(6, WEEK1, "rider", 8_000),
            *(earning(i, WEEK2, "kitchen", 10_000 * i) for i in range(1, 5)),
            earning(6, WEEK2, "rider", 8_000),
            earning(7, WEEK2, "kitchen", 1, data_source="synthetic"),
            community("donation_verified", TUESDAY1, items=3),
            community("donation_verified", TUESDAY1, items=2),
            community("referral_converted", TUESDAY1, source="referral"),
            community("society_live", TUESDAY2, source="society"),
        ]
    )
    await session.flush()
    if ran_at is not None:
        session.add(
            RefreshRun(
                job=refresh.REFRESH,
                scheduled_for=ran_at - timedelta(minutes=10),
                window_from=TUESDAY2 - timedelta(days=3),
                window_to=TUESDAY2 + timedelta(days=1),
                status="ok",
                started_at=ran_at - timedelta(minutes=10),
                finished_at=ran_at - timedelta(minutes=9),
            )
        )
    await session.flush()
    await refresh.refresh_views(session, refresh.VIEWS)


async def get_kpis(api: httpx2.AsyncClient, auth: dict[str, str], **params: Any) -> httpx2.Response:
    return await api.get(
        "/v1/analytics/kpis", params={"data_source": "real", **params}, headers=auth
    )


async def signed_in(
    clients: ClientFactory, google: FakeGoogle, phone: str
) -> tuple[httpx2.AsyncClient, dict[str, str]]:
    api: httpx2.AsyncClient = clients()
    auth, _ = await as_user(api, google, phone)
    return api, auth


def walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from walk_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from walk_keys(inner)


async def test_week_series_recompute_every_rate_from_its_parts(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await get_kpis(
        api, manager, hub_id=str(HUB), **{"from": "2026-10-26", "to": "2026-11-08"}
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert answer.headers["Cache-Control"] == "private, max-age=60"
    assert (body["hub_id"], body["data_source"], body["grain"]) == (str(HUB), "real", "week")
    assert body["periods"] == ["2026-10-26", "2026-11-02"]
    assert body["stale"] is False and body["as_of"] is not None
    assert list(body["series"]) == list(kpis.KPI_KEYS) == list(body["totals"])
    series, totals = body["series"], body["totals"]
    expected: dict[str, tuple[list[int | None], int | None]] = {
        # Distinct households with a delivered order: 2 then 1; the total is the last 7 days' (1)
        "active_households": ([2, 1], 1),
        # Cycles: 4 generated, 2 approved, 1 auto, 1 skipped | 1 generated, 1 approved
        "weeks_accepted_bp": ([5_000, 10_000], 6_000),
        "auto_approved_bp": ([2_500, 0], 2_000),
        "skipped_bp": ([2_500, 0], 2_000),
        "accepted_ml_bp": ([10_000, 10_000], 10_000),  # 2 of 2 ML cycles | 1 of 1
        "accepted_fallback_bp": ([0, None], 0),  # 0 of 2 fallback cycles | none that week
        "edits_per_100_boxes": ([50, 300], 133),  # 1 edit / 2 approved | 3 / 1; 4 / 3 over both
        "pantry_misses_bp": ([5_000, 0], 4_000),  # 2 of 4 taps | 0 of 1; 2 of 5 over both
        "orders_delivered": ([3, 1], 4),
        "boxes_delivered": ([2, 1], 3),
        "gmv_paise": ([125_000, 64_000], 189_000),  # net of the 2,000 refund
        "aov_paise": ([41_667, 64_000], 47_250),
        "on_time_bp": ([6_667, 10_000], 7_500),
        "first_attempt_bp": ([6_667, 5_000], 6_000),  # 2 of 3 dispatched | 1 of 2; 3 of 5
        "shortage_lines_bp": ([455, 909], 682),  # 1 of 22 lines | 2 of 22; 3 of 44
        # Bin grams only per 100 kg packed: 500 g of 100 kg | 0 of 50 kg; 500 g of 150 kg
        "waste_g_per_100kg": ([500, 0], 333),
        "delivery_cost_per_order_paise": ([10_000, 12_500], 11_000),  # box routes only
        "km_per_order_m": ([4_000, 4_000], 4_000),
        "issues_per_1000_orders": ([667, 0], 500),  # 2 issues / 3 delivered | none | 2 / 4
        "issue_auto_credit_bp": ([5_000, None], 5_000),
        "partner_earnings_paise": ([158_000, 108_000], 266_000),
        "partners_paid": ([6, 5], 6),  # the total counts a partner paid in both weeks once
        "kitchen_gini_bp": ([2_667, None], 2_240),  # 5 kitchens, then 4 (under 5)
        "donations_verified": ([2, 0], 2),
        "meals_donated": ([None, None], None),  # M9's pledges and hand-offs have no facade yet
        "referrals_converted": ([1, 0], 1),
        "societies_live": ([0, 1], 1),
    }
    assert set(expected) == set(kpis.KPI_KEYS)
    for key, (per_week, total) in expected.items():
        assert (series[key], totals[key]) == (per_week, total), key
    assert not PERSONAL & set(walk_keys(body))


async def test_the_gini_matches_the_weekly_view(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    view = (
        await db_session.execute(
            text(
                "SELECT gini_bp, partners_paid FROM analytics.mv_partner_earnings_weekly "
                "WHERE hub_id = :hub AND kind = 'kitchen' AND iso_week = :week "
                "AND data_source = 'real'"
            ),
            {"hub": HUB, "week": WEEK1},
        )
    ).one()
    assert (view.gini_bp, view.partners_paid) == (2_667, 5)
    assert kpis.gini_bp([10_000, 20_000, 30_000, 40_000, 50_000]) == view.gini_bp
    assert kpis.gini_bp([1, 2, 3, 4]) is None and kpis.gini_bp([]) is None


async def test_day_grain_has_one_period_a_day_and_no_weekly_keys(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await get_kpis(
        api, owner, hub_id=str(HUB), grain="day", **{"from": "2026-11-02", "to": "2026-11-04"}
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["periods"] == ["2026-11-02", "2026-11-03", "2026-11-04"]
    series, totals = body["series"], body["totals"]
    assert series["orders_delivered"] == [0, 1, 0]
    assert series["gmv_paise"] == [0, 64_000, 0]
    assert series["active_households"] == [0, 1, 0]
    assert series["on_time_bp"] == [None, 10_000, None]  # nothing delivered, nothing to divide
    assert series["weeks_accepted_bp"] == [None, 10_000, None]
    # Nothing packed is no rate; 50 kg packed and only an NGO hand-over is 0 waste (M6 §3.5)
    assert series["waste_g_per_100kg"] == [None, 0, None]
    assert (totals["orders_delivered"], totals["active_households"]) == (1, 1)
    for weekly in kpis.WEEKLY_ONLY:  # a day cannot be cut out of a week of earnings
        assert series[weekly] == [None] * 3 and totals[weekly] is None


async def test_data_sources_are_never_pooled_and_one_is_required(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)
    window = {"from": "2026-10-26", "to": "2026-11-08", "hub_id": str(HUB)}

    synthetic = await api.get(
        "/v1/analytics/kpis", params={**window, "data_source": "synthetic"}, headers=manager
    )

    assert synthetic.status_code == 200, synthetic.text
    series = synthetic.json()["series"]
    assert synthetic.json()["data_source"] == "synthetic"
    assert series["orders_delivered"] == [0, 1] and series["gmv_paise"] == [0, 99_999]
    assert series["weeks_accepted_bp"] == [None, 10_000]  # its one cycle, none of the real ones
    assert series["partner_earnings_paise"] == [0, 1]
    assert series["waste_g_per_100kg"] == [None, None]  # the real hub's packing and waste
    real = await get_kpis(api, manager, **window)
    assert real.json()["series"]["gmv_paise"] == [125_000, 64_000]  # no 99,999 in a real series

    missing = await api.get("/v1/analytics/kpis", params=window, headers=manager)
    assert missing.status_code == 422
    assert missing.json()["code"] == "analytics.data_source_required"
    pilot = await api.get(
        "/v1/analytics/kpis", params={**window, "data_source": "pilot"}, headers=manager
    )
    assert pilot.status_code == 422  # the stored values only: real | synthetic


async def test_hub_scope_and_roles(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)
    window = {"from": "2026-10-26", "to": "2026-11-08"}

    elsewhere = await get_kpis(api, manager, hub_id=str(OTHER_HUB), **window)
    assert elsewhere.status_code == 403 and elsewhere.json()["code"] == "auth.forbidden"
    left_out = await get_kpis(api, manager, **window)  # a non-admin must name the hub
    assert left_out.status_code == 403 and left_out.json()["code"] == "auth.forbidden"
    chosen = await get_kpis(
        api, {**manager, "X-Active-Role": f"hub_manager:{HUB}"}, hub_id=str(HUB), **window
    )
    assert chosen.status_code == 200, chosen.text
    not_held = await get_kpis(api, {**manager, "X-Active-Role": "admin"}, hub_id=str(HUB), **window)
    assert not_held.status_code == 403 and not_held.json()["code"] == "auth.role_not_granted"

    customer_api, customer = await signed_in(clients, google, PRIYA)
    refused = await get_kpis(customer_api, customer, hub_id=str(HUB), **window)
    assert refused.status_code == 403
    anonymous = await api.get("/v1/analytics/kpis", params={"data_source": "real"})
    assert anonymous.status_code == 401

    admin_api, admin = await signed_in(clients, google, NEHA)
    one = await get_kpis(admin_api, admin, hub_id=str(HUB), **window)
    every = await get_kpis(admin_api, admin, **window)
    assert every.status_code == 200, every.text
    assert every.json()["hub_id"] is None
    # The other hub's delivered order joins in, within the real source only
    assert one.json()["series"]["orders_delivered"] == [3, 1]
    assert every.json()["series"]["orders_delivered"] == [4, 1]
    assert every.json()["series"]["gmv_paise"] == [195_000, 64_000]


async def test_a_box_route_without_a_recorded_cost_has_no_cost_per_order(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    answer = await get_kpis(
        api,
        admin,
        hub_id=str(OTHER_HUB),
        grain="day",
        **{"from": "2026-11-03", "to": "2026-11-03"},
    )

    assert answer.status_code == 200, answer.text
    series = answer.json()["series"]
    assert series["delivery_cost_per_order_paise"] == [None]  # a plan without a cost is not free
    assert series["km_per_order_m"] == [1]


@pytest.mark.parametrize(
    ("grain", "days", "status", "code"),
    [
        ("day", 92, 200, None),
        ("day", 93, 422, "analytics.range_too_long"),
        ("week", 104 * 7, 200, None),
        # A week series is whole ISO weeks: one day over 104 weeks reaches into the 105th
        ("week", 104 * 7 + 1, 422, "analytics.range_too_long"),
        ("week", 105 * 7, 422, "analytics.range_too_long"),
    ],
)
async def test_range_limits(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    grain: str,
    days: int,
    status: int,
    code: str | None,
) -> None:
    api, admin = await signed_in(clients, google, NEHA)
    start = date(2025, 1, 6)  # a Monday: a whole number of weeks stays that many

    answer = await get_kpis(
        api,
        admin,
        grain=grain,
        **{"from": start.isoformat(), "to": (start + timedelta(days=days - 1)).isoformat()},
    )

    assert answer.status_code == status, answer.text
    if code:
        assert answer.json()["code"] == code
    else:
        periods = answer.json()["periods"]
        assert len(periods) == (days if grain == "day" else -(-days // 7))


async def test_a_backwards_range_and_the_defaults(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_facts(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    backwards = await get_kpis(api, admin, **{"from": "2026-11-03", "to": "2026-11-02"})
    assert backwards.status_code == 422 and backwards.json()["code"] == "analytics.range_invalid"

    default = await get_kpis(api, admin, hub_id=str(HUB))
    assert default.status_code == 200, default.text
    periods = default.json()["periods"]
    assert len(periods) == 12 and periods[-1] == "2026-11-02"  # the last 12 weeks, this one last
    assert periods[0] == "2026-08-17"
    daily = await get_kpis(api, admin, hub_id=str(HUB), grain="day")
    assert len(daily.json()["periods"]) == 84 and daily.json()["periods"][-1] == "2026-11-03"


async def test_as_of_and_stale_follow_the_last_run(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, admin = await signed_in(clients, google, NEHA)
    never = (await get_kpis(api, admin, hub_id=str(HUB))).json()
    assert (never["as_of"], never["stale"]) == (None, True)  # no run has covered it

    await seed_facts(db_session, ran_at=NOW - timedelta(minutes=50))
    old = (await get_kpis(api, admin, hub_id=str(HUB))).json()
    assert old["stale"] is True and old["as_of"] is not None  # 15-minute view: 45 minutes at most

    db_session.add(
        RefreshRun(
            job=refresh.REFRESH,
            scheduled_for=NOW,
            window_from=TUESDAY2,
            window_to=TUESDAY2,
            status="ok",
            started_at=NOW - timedelta(minutes=2),
            finished_at=NOW - timedelta(minutes=1),
        )
    )
    await db_session.flush()
    fresh = (await get_kpis(api, admin, hub_id=str(HUB))).json()
    assert fresh["stale"] is False
    assert datetime.fromisoformat(fresh["as_of"]) == NOW - timedelta(minutes=1)
    run = (await db_session.execute(select(RefreshRun).limit(1))).scalar_one()
    assert (await freshness.freshness(db_session, "mv_daily_hub_kpis")).stale is False
    assert run.job == refresh.REFRESH


async def test_ratios_round_half_up_and_need_a_denominator() -> None:
    assert kpis.ratio(1, 3, 10_000) == 3_333 and kpis.ratio(2, 3, 10_000) == 6_667
    assert kpis.ratio(1, 2) == 1 and kpis.ratio(1, 4) == 0 and kpis.ratio(3, 4) == 1
    assert kpis.ratio(5, 0, 10_000) is None and kpis.ratio(0, 0) is None
    assert kpis.ratio(0, 7, 10_000) == 0


async def test_a_read_over_three_seconds_is_a_timeout_problem(db_session: AsyncSession) -> None:
    async def slow() -> None:
        async with reads.read_limit(db_session):
            await db_session.execute(text("SET LOCAL statement_timeout = '20ms'"))
            await db_session.execute(text("SELECT pg_sleep(2)"))

    with pytest.raises(ProblemError) as caught:
        await slow()
    assert (caught.value.status, caught.value.code) == (503, "analytics.timeout")


async def test_the_read_timeout_ends_with_the_requests_transaction(database_url: str) -> None:
    """`SET LOCAL` lasts only for the transaction: the next request on the same pooled
    connection, or the same session after its commit, runs under the server's default again."""
    url, connect_args = asyncpg_url(database_url)
    engine = create_async_engine(url, connect_args=connect_args, pool_size=1, max_overflow=0)
    try:
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            default = await session.scalar(text("SHOW statement_timeout"))
            assert default != reads.STATEMENT_TIMEOUT
            await session.rollback()
            async with reads.read_limit(session):
                assert await session.scalar(text("SHOW statement_timeout")) == "3s"
            await session.commit()
            assert await session.scalar(text("SHOW statement_timeout")) == default
            async with reads.read_limit(session):  # left open: the request ends mid-transaction
                assert await session.scalar(text("SHOW statement_timeout")) == "3s"
        async with sessions() as other:  # the next request, on the one pooled connection
            assert await other.scalar(text("SHOW statement_timeout")) == default
    finally:
        await engine.dispose()


async def test_the_per_user_rate_limit(
    clients: ClientFactory,
    google: FakeGoogle,
    valkey: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(router, "REQUESTS_PER_MINUTE", 2)
    api: httpx2.AsyncClient = clients(valkey=valkey)
    admin, _ = await as_user(api, google, NEHA)

    statuses = [(await get_kpis(api, admin, hub_id=str(HUB))).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    limited = await get_kpis(api, admin, hub_id=str(HUB))
    assert limited.json()["code"] == "request.rate_limited" and "Retry-After" in limited.headers


# --- societies ---------------------------------------------------------------------------------


async def seed_society_facts(session: AsyncSession) -> None:
    plan_id = (await session.execute(select(Plan.id).where(Plan.code == "autopilot"))).scalar_one()
    old = TUESDAY2 - timedelta(days=40)
    session.add_all(
        [
            *(
                FactHousehold(
                    household_id=uuid.UUID(int=i),
                    hub_id=HUB,
                    society_id=SOCIETY,
                    created_on=old,
                    cohort_week=monday(old),
                    status="active",
                    data_source="synthetic",
                )
                for i in range(1, 5)
            ),
            # Of the 4 homes: 1 and 2 had a delivery in the last 4 weeks, 3 is paused, 4 is idle
            order(TUESDAY2, household_id=uuid.UUID(int=1), data_source="synthetic"),
            order(
                TUESDAY1,
                household_id=uuid.UUID(int=2),
                total_paise=64_000,
                refunded_paise=2_000,
                data_source="synthetic",
            ),
            order(
                TUESDAY1,
                household_id=uuid.UUID(int=2),
                kind="addon",
                total_paise=9_000,
                data_source="synthetic",
            ),
            order(old, household_id=uuid.UUID(int=4), data_source="synthetic"),  # too long ago
            order(TUESDAY2, household_id=uuid.UUID(int=1)),  # real: not in the synthetic table
            # Cycles of the last 4 weeks: 3 of 4 approved by the cut-off
            *(cycle(TUESDAY1, data_source="synthetic") for _ in range(3)),
            cycle(TUESDAY1, approved_before_cutoff=False, data_source="synthetic"),
            cycle(old, data_source="synthetic"),
            Subscription(
                household_id=uuid.UUID(int=3),
                plan_id=plan_id,
                payment_mode="wallet",
                status="paused",
            ),
            Subscription(household_id=uuid.UUID(int=4), plan_id=plan_id, payment_mode="wallet"),
        ]
    )
    await session.flush()
    session.add(
        RefreshRun(
            job=refresh.NIGHTLY,
            scheduled_for=NOW,
            window_from=TUESDAY2,
            window_to=TUESDAY2,
            status="ok",
            started_at=NOW - timedelta(minutes=3),
            finished_at=NOW - timedelta(minutes=2),
        )
    )
    await session.flush()


async def test_the_societies_table(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_society_facts(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await api.get(
        "/v1/analytics/societies",
        params={"hub_id": str(HUB), "data_source": "synthetic"},
        headers=manager,
    )

    assert answer.status_code == 200, answer.text
    assert answer.headers["Cache-Control"] == "private, max-age=60"
    body = answer.json()
    assert body["hub_id"] == str(HUB) and body["as_of"] is not None
    listed = await community_api.hub_societies(db_session, hub_id=HUB)
    assert [row["name"] for row in body["items"]] == [s.name for s in listed]
    rows = {row["society_id"]: row for row in body["items"]}
    sunrise = rows[str(SOCIETY)]
    seed = next(s for s in listed if s.id == SOCIETY)
    assert sunrise == {
        "society_id": str(SOCIETY),
        "name": "Sunrise Towers",
        "status": "live",
        "joined": seed.joined,
        "threshold": seed.threshold,
        "active_households": 2,  # homes 1 and 2 had a delivery in the last 4 weeks
        "paused_households": 1,  # home 3; home 4's subscription is active
        "approved_on_time_bp": 7_500,
        "average_box_paise": 61_000,  # (60,000 + 64,000 - 2,000) / 2 boxes; add-ons are not boxes
    }
    other = next(row for row in body["items"] if row["society_id"] != str(SOCIETY))
    assert other["active_households"] == 0 and other["paused_households"] == 0
    assert other["approved_on_time_bp"] is None and other["average_box_paise"] is None
    assert not PERSONAL & set(walk_keys(body))

    real = await api.get(
        "/v1/analytics/societies",
        params={"hub_id": str(HUB), "data_source": "real"},
        headers=manager,
    )
    assert real.json()["items"] == []  # the seed societies are synthetic: never pooled


async def test_societies_scope_and_data_source(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_society_facts(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)
    params = {"data_source": "synthetic"}

    assert (
        await api.get("/v1/analytics/societies", params=params, headers=manager)
    ).status_code == 403
    elsewhere = await api.get(
        "/v1/analytics/societies", params={**params, "hub_id": str(OTHER_HUB)}, headers=manager
    )
    assert elsewhere.status_code == 403
    missing = await api.get("/v1/analytics/societies", params={"hub_id": str(HUB)}, headers=manager)
    assert missing.status_code == 422
    assert missing.json()["code"] == "analytics.data_source_required"

    admin_api, admin = await signed_in(clients, google, NEHA)
    everything = await admin_api.get("/v1/analytics/societies", params=params, headers=admin)
    assert everything.status_code == 200, everything.text
    assert everything.json()["hub_id"] is None
    assert str(SOCIETY) in {row["society_id"] for row in everything.json()["items"]}
