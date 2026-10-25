"""The analytics refresh (M12 §3.2) over stand-in facades: upserts change a fact only when a value
changed, the source wins, failed pulls keep the facts, runs are recorded, and the views are
refreshed on their unique grain."""

import dataclasses
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import freshness, refresh
from gharbasket.modules.analytics.models import (
    FactCommunity,
    FactHousehold,
    FactOrder,
    FactPartnerWeek,
    RefreshRun,
)
from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.subscriptions import api as subs_api
from gharbasket.platform.jobs import run_job

from ..conftest import make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.services]

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = date(2026, 11, 3)
AT = datetime.combine(TUESDAY, time(10, 0), IST)  # a 15-minute run on Tuesday morning
HUB = uuid.uuid4()
SOCIETY = uuid.uuid4()


class Source:
    """What each stand-in facade answers, filtered by the window it is asked for."""

    def __init__(self) -> None:
        self.households: list[identity_api.HouseholdFact] = []
        self.orders: list[orders_api.OrderFact] = []
        self.cycles: list[subs_api.CycleFact] = []
        self.donations: list[community_api.ImpactFact] = []
        self.earnings: list[billing_api.EarningFact] = []
        self.partners: list[partners_api.PartnerFact] = []
        self.calls: list[tuple[str, date, date]] = []
        self.broken = False

    def dated[T](self, name: str, rows: list[T], field: str, kw: dict[str, Any]) -> list[T]:
        if self.broken and name == "orders":
            raise RuntimeError("orders facade down")
        self.calls.append((name, kw["date_from"], kw["date_to"]))
        return [r for r in rows if kw["date_from"] <= getattr(r, field) <= kw["date_to"]]


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> Iterator[Source]:
    src = Source()

    async def none(_session: AsyncSession, **_kw: Any) -> list[Any]:
        return []

    async def households(_session: AsyncSession, **_kw: Any) -> list[Any]:
        return list(src.households)

    async def orders(_session: AsyncSession, **kw: Any) -> list[Any]:
        return src.dated("orders", src.orders, "delivery_date", kw)

    async def cycles(_session: AsyncSession, **kw: Any) -> list[Any]:
        return src.dated("cycles", src.cycles, "delivery_date", kw)

    async def impact(_session: AsyncSession, **kw: Any) -> list[Any]:
        return src.dated("community", src.donations, "on", kw)

    async def earnings(_session: AsyncSession, **kw: Any) -> list[Any]:
        return list(src.earnings)

    async def partners(_session: AsyncSession, **kw: Any) -> list[Any]:
        return [p for p in src.partners if p.partner_id in kw["ids"]]

    for module, name, fake in (
        (identity_api, "household_facts", households),
        (subs_api, "cycle_facts", cycles),
        (orders_api, "order_facts", orders),
        (orders_api, "issue_facts", none),
        (hub_ops_api, "hub_day_facts", none),
        (logistics_api, "route_facts", none),
        (community_api, "impact_facts", impact),
        (community_api, "referral_facts", none),
        (billing_api, "earning_facts", earnings),
        (partners_api, "partner_facts", partners),
        (partners_api, "assignment_run_facts", none),
    ):
        monkeypatch.setattr(module, name, fake)
    yield src


def order(delivery: date = TUESDAY, **values: Any) -> orders_api.OrderFact:
    defaults: dict[str, Any] = {
        "order_id": uuid.uuid4(),
        "household_id": uuid.uuid4(),
        "hub_id": HUB,
        "society_id": SOCIETY,
        "kind": "box",
        "delivery_date": delivery,
        "slot_end": datetime.combine(delivery, time(9), IST),
        "status": "delivered",
        "total_paise": 63_000,
        "fees_paise": 3_000,
        "refunded_paise": 0,
        "credited_paise": 1_000,
        "lines": 10,
        "short_lines": 1,
        "delivered_at": datetime.combine(delivery, time(8, 30), IST),
        "attempts": 1,
        "data_source": "real",
    }
    return orders_api.OrderFact(**(defaults | values))


async def facts(session: AsyncSession) -> dict[uuid.UUID, FactOrder]:
    rows = await session.execute(select(FactOrder).execution_options(populate_existing=True))
    return {row.order_id: row for row in rows.scalars()}


async def runs(session: AsyncSession) -> list[RefreshRun]:
    found = await session.execute(
        select(RefreshRun)
        .order_by(RefreshRun.started_at, RefreshRun.id)
        .execution_options(populate_existing=True)
    )
    return list(found.scalars())


async def test_a_fact_changes_only_when_a_value_changed(
    db_session: AsyncSession, source: Source
) -> None:
    kept, edited, gone = order(), order(), order()
    source.orders = [kept, edited, gone, order(TUESDAY + timedelta(days=5))]  # outside
    first = datetime(2026, 11, 3, 4, 30, tzinfo=UTC)

    summary = await refresh.run(db_session, refresh.REFRESH, AT, now=first)

    assert summary["read"]["orders"] == 3 and summary["changed"]["orders"] == 3
    assert summary["window"] == ["2026-10-31", "2026-11-04"]
    stored = await facts(db_session)
    assert set(stored) == {kept.order_id, edited.order_id, gone.order_id}
    assert stored[kept.order_id].on_time is True and stored[kept.order_id].first_attempt is True

    again = await refresh.run(db_session, refresh.REFRESH, AT, now=first + timedelta(minutes=15))
    assert again["changed"]["orders"] == 0 and again["removed"]["orders"] == 0
    assert {f.synced_at for f in (await facts(db_session)).values()} == {first}

    late = datetime.combine(TUESDAY, time(9, 5), IST)
    source.orders = [kept, dataclasses.replace(edited, delivered_at=late, attempts=2)]
    third = first + timedelta(minutes=30)
    summary = await refresh.run(db_session, refresh.REFRESH, AT, now=third)

    assert (summary["changed"]["orders"], summary["removed"]["orders"]) == (1, 1)
    stored = await facts(db_session)
    assert set(stored) == {kept.order_id, edited.order_id}  # the source wins: `gone` went
    assert stored[kept.order_id].synced_at == first
    assert stored[edited.order_id].synced_at == third
    assert (stored[edited.order_id].on_time, stored[edited.order_id].first_attempt) == (
        False,
        False,
    )
    assert [r.status for r in await runs(db_session)] == ["ok", "ok", "ok"]


async def test_a_failed_pull_keeps_the_facts_and_records_the_run_failed(
    db_session: AsyncSession, source: Source
) -> None:
    source.orders = [order()]
    await refresh.run(db_session, refresh.REFRESH, AT)
    before = await facts(db_session)

    source.orders = [order(), order()]
    source.cycles = []
    source.broken = True
    run = await run_job(
        db_session, refresh.REFRESH, settings=make_settings(), scheduled_for=AT + timedelta(hours=1)
    )

    assert run.status == "failed" and run.error is not None and "RefreshFailed" in run.error
    assert set(await facts(db_session)) == set(before)
    recorded = await runs(db_session)
    assert [r.status for r in recorded] == ["ok", "failed"]
    failed = recorded[-1]
    assert failed.error == "RuntimeError: orders facade down"
    assert failed.finished_at is not None and failed.rows["read"]["cycles"] == 0
    # The failed run is not an `as_of`: the 15-minute views keep the first run's time.
    found = await freshness.freshness(db_session, "mv_daily_hub_kpis", now=recorded[0].finished_at)
    assert found == freshness.Freshness(recorded[0].finished_at, False)

    source.broken = False
    retried = await run_job(
        db_session, refresh.REFRESH, settings=make_settings(), scheduled_for=AT + timedelta(hours=2)
    )
    assert retried.status == "ok"
    assert len(await facts(db_session)) == 2  # the two the source has now


async def test_community_facts_are_pulled_since_the_last_run(
    db_session: AsyncSession, source: Source
) -> None:
    def donation(on: date) -> community_api.ImpactFact:
        return community_api.ImpactFact(
            source="donation",
            source_id=uuid.uuid4(),
            kind="donation_verified",
            hub_id=HUB,
            society_id=SOCIETY,
            ngo_id=None,
            on=on,
            items=4,
            kg_tenths=25,
            meals=0,
            points=130,
            people_reached=None,
            household_id=uuid.uuid4(),
            data_source="real",
        )

    week_ago = TUESDAY - timedelta(days=7)
    source.donations = [donation(TUESDAY), donation(week_ago)]
    first = await refresh.run(db_session, refresh.REFRESH, AT - timedelta(days=10))
    assert first["read"]["community"] == 0  # 10 days earlier: neither is in its window yet

    # The next run comes 10 days later: community facts since the last run's day are pulled.
    await refresh.run(db_session, refresh.REFRESH, AT)
    assert ("community", TUESDAY - timedelta(days=10), TUESDAY + timedelta(days=1)) in source.calls
    rows = (await db_session.execute(select(FactCommunity))).scalars().all()
    assert sorted(r.on_date for r in rows) == [week_ago, TUESDAY]

    # A run right after only looks back the usual three days.
    await refresh.run(db_session, refresh.REFRESH, AT + timedelta(minutes=15))
    assert source.calls[-1] == ("community", TUESDAY - timedelta(days=3), TUESDAY + timedelta(1))


async def test_the_nightly_run_refreshes_households_partner_weeks_and_every_view(
    db_session: AsyncSession, source: Source
) -> None:
    household = identity_api.HouseholdFact(
        household_id=uuid.uuid4(),
        hub_id=HUB,
        society_id=SOCIETY,
        created_on=date(2026, 10, 21),  # a Wednesday: cohort week Monday 19 Oct
        status="active",
        data_source="real",
    )
    other = dataclasses.replace(household, household_id=uuid.uuid4())
    source.households = [household, other]
    cooks = [
        partners_api.PartnerFact(uuid.uuid4(), HUB, "kitchen", "active", "real") for _ in range(5)
    ]
    source.partners = cooks
    week = "2026-W45"
    source.earnings = [
        billing_api.EarningFact(cook.partner_id, week, "tiffin_portion", n, 1_000 * n)
        for n, cook in enumerate(cooks, start=1)
    ] + [billing_api.EarningFact(uuid.uuid4(), week, "bag_delivered", 1, 600)]  # unknown partner
    # Delivered in the household's cohort week + 1 and + 2.
    source.orders = [
        order(date(2026, 10, 27), household_id=household.household_id),
        order(date(2026, 11, 3), household_id=household.household_id),
        order(date(2026, 11, 3), household_id=other.household_id, status="undelivered"),
    ]
    nightly = datetime.combine(TUESDAY, time(2, 40), IST)

    summary = await refresh.run(db_session, refresh.NIGHTLY, nightly)

    assert summary["window"] == ["2026-09-28", "2026-11-03"]
    assert summary["views"] == list(refresh.VIEWS)
    assert summary["read"]["partner_weeks"] == 5
    assert summary["read"]["partner_weeks_unmatched"] == 1
    cohort = await db_session.execute(
        text(
            "SELECT week_offset, households, active FROM analytics.mv_cohorts "
            "WHERE hub_id = :hub AND week_offset <= 3 ORDER BY week_offset"
        ),
        {"hub": HUB},
    )
    assert cohort.all() == [(1, 2, 1), (2, 2, 1), (3, 2, 0)]
    stored = (await db_session.execute(select(FactHousehold))).scalars().all()
    assert {h.cohort_week for h in stored} == {date(2026, 10, 19)}
    earnings = await db_session.execute(
        text(
            "SELECT partners_paid, total_paise, p10_paise, median_paise, p90_paise, gini_bp "
            "FROM analytics.mv_partner_earnings_weekly WHERE hub_id = :hub AND kind = 'kitchen'"
        ),
        {"hub": HUB},
    )
    # 1,000 to 5,000 paise: Gini = 2·55,000 / (5·15,000) - 6/5 = 0.2667
    assert earnings.all() == [(5, 15_000, 1_400, 3_000, 4_600, 2_667)]
    weeks = (await db_session.execute(select(FactPartnerWeek))).scalars().all()
    assert {w.iso_week for w in weeks} == {date(2026, 11, 2)}

    # A purged household leaves at the next full pull.
    source.households = [household]
    summary = await refresh.run(db_session, refresh.NIGHTLY, nightly + timedelta(days=1))
    assert summary["removed"]["households"] == 1


async def test_the_daily_view_adds_up_a_hub_day_per_data_source(
    db_session: AsyncSession, source: Source
) -> None:
    source.orders = [
        order(),
        order(credited_paise=0, delivered_at=datetime.combine(TUESDAY, time(9, 30), IST)),
        order(kind="addon", status="refunded", refunded_paise=5_000, attempts=2, delivered_at=None),
        order(data_source="synthetic"),
    ]

    await refresh.run(db_session, refresh.REFRESH, AT)

    rows = await db_session.execute(
        text(
            "SELECT data_source, orders_delivered, boxes_delivered, households_active, gmv_paise,"
            " refunds_paise, on_time_orders, first_attempt_orders, dispatched_orders,"
            " short_lines, packed_lines FROM analytics.mv_daily_hub_kpis"
            " WHERE hub_id = :hub AND date = :day ORDER BY data_source"
        ),
        {"hub": HUB, "day": TUESDAY},
    )
    assert rows.all() == [
        ("real", 2, 2, 2, 62_000 + 63_000, 7_000, 1, 2, 3, 3, 30),
        ("synthetic", 1, 1, 1, 62_000, 1_000, 1, 1, 1, 1, 10),
    ]


async def test_every_view_has_a_unique_index_on_its_grain(db_session: AsyncSession) -> None:
    found = await db_session.execute(
        text(
            "SELECT tablename, indexdef FROM pg_indexes "
            "WHERE schemaname = 'analytics' AND tablename LIKE 'mv\\_%'"
        )
    )
    indexes = dict(found.all())
    assert set(indexes) == set(refresh.VIEWS)
    assert all("UNIQUE INDEX" in definition for definition in indexes.values())
    # CONCURRENTLY needs that index; it works on empty views too.
    await refresh.refresh_views(db_session, refresh.VIEWS)
