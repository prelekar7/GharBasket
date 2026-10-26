"""M12 §9 on a small seeded scenario through the real facades: the incremental runs and a full
rebuild give the same views, a rerun changes nothing, and M12 keeps to its own schema."""

import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import models, refresh
from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community.models import Donation
from gharbasket.modules.hub_ops.models import PackingLine, PackingTask
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import Household
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, Vehicle
from gharbasket.modules.orders.models import Order, OrderEvent, OrderIssue, OrderLine
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.subscriptions.models import (
    EditFeedback,
    PantryFeedback,
    Plan,
    Subscription,
    WeeklyCycle,
)
from gharbasket.platform.db import Base
from gharbasket.tools.seed import SEED_HUB_ID, seed_delivery, seed_hubs, seed_partners

from ..catalog.conftest import world  # noqa: F401

pytestmark = [pytest.mark.anyio, pytest.mark.services]

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = date(2026, 11, 3)
SRC = Path(__file__).resolve().parents[2] / "src" / "gharbasket" / "modules" / "analytics"
MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "migrations" / "versions").glob("*_analytics_facts.py")
)


async def _scenario(session: AsyncSession) -> None:
    """Three seeded households with a Tuesday box each: delivered (one late, one with a credited
    issue), their cycles and pantry taps, the packing, a published route, a verified donation and
    a captain's earnings."""
    await seed_hubs(session)
    await seed_partners(session)
    slot_id, _ = await seed_delivery(session)
    # Signed up on a fixed day, so the rebuild starts there whatever the clock says.
    signed_up = datetime(2026, 10, 20, 6, tzinfo=IST)
    await session.execute(update(Household).values(created_at=signed_up))
    households = [h for h in await identity_api.household_facts(session) if h.hub_id][:3]
    plan_id = (await session.execute(select(Plan.id).where(Plan.code == "autopilot"))).scalar_one()
    cutoff = datetime.combine(TUESDAY - timedelta(days=1), time(21), IST)
    van = Vehicle(
        hub_id=SEED_HUB_ID,
        label="EV-T",
        kind="cargo_3w",
        capacity_kg=500,
        capacity_crates=20,
        capacity_tiffins=0,
    )
    route_plan = RoutePlan(
        hub_id=SEED_HUB_ID,
        delivery_date=TUESDAY,
        kind="box",
        status="published",
        trigger="job",
        source="fallback_nearest",
        summary={"cost_paise": 41_000},
    )
    session.add_all([van, route_plan])
    await session.flush()
    route = Route(
        plan_id=route_plan.id,
        hub_id=SEED_HUB_ID,
        delivery_date=TUESDAY,
        kind="box",
        trip=1,
        vehicle_id=van.id,
        status="completed",
        depart_time=time(6, 30),
        return_by=time(10),
        planned_km=12.5,
    )
    session.add(route)
    await session.flush()
    for n, household in enumerate(households):
        subscription = Subscription(
            household_id=household.household_id, plan_id=plan_id, payment_mode="wallet"
        )
        session.add(subscription)
        await session.flush()
        cycle = WeeklyCycle(
            household_id=household.household_id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            subscription_id=subscription.id,
            week_start=TUESDAY - timedelta(days=1),
            delivery_date=TUESDAY,
            slot_id=slot_id,
            cutoff_at=cutoff,
            plan_source="ml" if n else "fallback",
            status="locked",
            approved_at=cutoff - timedelta(hours=n + 1),
            auto_approved=n == 2,
            data_source=household.data_source,
        )
        order = Order(
            household_id=household.household_id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            kind="box",
            delivery_date=TUESDAY,
            slot_id=slot_id,
            cutoff_at=cutoff,
            status="delivered",
            subtotal_paise=60_000 + n * 1_000,
            fees_paise=0,
            total_paise=60_000 + n * 1_000,
            data_source=household.data_source,
        )
        session.add_all([cycle, order])
        await session.flush()
        product = uuid.uuid4()
        delivered = datetime.combine(TUESDAY, time(8, 0) if n < 2 else time(9, 40), IST)
        task = PackingTask(
            hub_id=SEED_HUB_ID,
            order_id=order.id,
            order_kind="box",
            delivery_date=TUESDAY,
            slot_id=slot_id,
            society_id=household.society_id,
            seq=n + 1,
            status="packed",
        )
        session.add_all(
            [
                OrderLine(
                    order_id=order.id,
                    product_id=product,
                    kind="box",
                    qty=4,
                    step_qty=500,
                    step_unit="g",
                    unit_price_paise=15_000,
                    shortage_qty=1 if n == 1 else 0,
                ),
                OrderEvent(order_id=order.id, to_status="dispatched", actor="rider"),
                OrderEvent(order_id=order.id, to_status="delivered", actor="captain", at=delivered),
                EditFeedback(cycle_id=cycle.id, product_id=product, action="qty_up"),
                PantryFeedback(
                    cycle_id=cycle.id,
                    household_id=household.household_id,
                    product_id=product,
                    signal=("ran_out", "wasted", "just_right")[n],
                ),
                task,
            ]
        )
        await session.flush()
        session.add(
            PackingLine(
                task_id=task.id,
                product_id=product,
                step_qty=500,
                step_unit="g",
                qty=4,
                picked_qty=4 - (1 if n == 1 else 0),
                short_qty=1 if n == 1 else 0,
            )
        )
        if n == 0:
            session.add(
                OrderIssue(
                    order_id=order.id,
                    household_id=household.household_id,
                    hub_id=SEED_HUB_ID,
                    kind="damaged",
                    status="credited",
                    resolved_by="auto",
                    credit_paise=1_500,
                    amount_paise=1_500,
                    photo_keys=[],
                    created_by=uuid.uuid4(),
                    created_at=delivered + timedelta(hours=2),
                    resolved_at=delivered + timedelta(hours=2),
                )
            )
            session.add(
                Donation(
                    user_id=uuid.uuid4(),
                    household_id=household.household_id,
                    hub_id=SEED_HUB_ID,
                    society_id=household.society_id,
                    items=[{"code": "books", "unit": "each", "qty": 3, "counted": 3}],
                    status="verified",
                    pickup_on=TUESDAY,
                    estimated_points=30,
                    points_awarded=30,
                    verified_at=delivered + timedelta(hours=4),
                    data_source=household.data_source,
                )
            )
        session.add(
            RouteStop(
                route_id=route.id,
                seq=n + 1,
                society_id=household.society_id,
                window_end=time(9),
                eta=time(8),
                orders=1,
                handed_over_at=delivered,
            )
        )
        captains = [p for p in await partners_api.partner_facts(session) if p.kind == "captain"]
        await billing_api.accrue_earning(
            session,
            partner_id=captains[n % len(captains)].partner_id,
            basis="bag_delivered",
            ref_type="order",
            ref_id=order.id,
            units=1,
            amount_paise=600,
            earned_at=delivered,
        )
    await session.flush()


async def _views(session: AsyncSession) -> dict[str, list[tuple[object, ...]]]:
    return {
        name: [
            tuple(row)
            for row in await session.execute(
                text(f"SELECT * FROM analytics.{name} ORDER BY 1, 2, 3, 4")
            )
        ]
        for name in refresh.VIEWS
    }


@pytest.mark.usefixtures("world")
async def test_a_rebuild_from_the_facades_gives_the_same_numbers(db_session: AsyncSession) -> None:
    await _scenario(db_session)
    # The day as the schedule runs it: a 15-minute run on Tuesday, the nightly run on Wednesday.
    await refresh.run(db_session, refresh.REFRESH, datetime.combine(TUESDAY, time(10), IST))
    nightly_at = datetime.combine(TUESDAY + timedelta(days=1), time(2, 40), IST)
    await refresh.run(db_session, refresh.NIGHTLY, nightly_at)
    incremental = await _views(db_session)

    kpis = incremental["mv_daily_hub_kpis"]
    tuesday = [row for row in kpis if row[0] == SEED_HUB_ID and row[1] == TUESDAY]
    assert len(tuesday) == 1  # the seed is one data source: synthetic
    empty = await db_session.execute(text("SELECT * FROM analytics.mv_daily_hub_kpis LIMIT 0"))
    names = list(empty.keys())
    day = dict(zip(names, tuesday[0], strict=True))
    assert day["data_source"] == "synthetic"
    assert (day["orders_delivered"], day["households_active"], day["on_time_orders"]) == (3, 3, 2)
    assert (day["gmv_paise"], day["refunds_paise"]) == (183_000 - 1_500, 1_500)
    assert (day["cycles_generated"], day["cycles_approved"], day["cycles_ml"]) == (3, 3, 2)
    assert (day["line_edits"], day["pantry_taps"], day["ran_out_taps"]) == (3, 3, 1)
    assert (day["short_lines"], day["packed_lines"], day["packed_g"]) == (1, 3, 5_500)
    assert (day["issues"], day["issues_auto_credited"], day["donations_verified"]) == (1, 1, 1)
    assert (day["box_route_km_m"], day["box_route_cost_paise"], day["box_route_orders"]) == (
        12_500,
        41_000,
        3,
    )
    assert incremental["mv_partner_earnings_weekly"]  # the captains' bags
    assert incremental["mv_cohorts"] and incremental["mv_weekly_unit_economics"]

    # A rerun changes nothing.
    again = await refresh.run(db_session, refresh.NIGHTLY, nightly_at)
    assert set(again["changed"].values()) == {0} and set(again["removed"].values()) == {0}

    for model in models.FACT_TABLES:
        await db_session.execute(delete(model))
    await refresh.run(db_session, refresh.REBUILD, nightly_at)

    assert await _views(db_session) == incremental


def test_m12_names_no_other_schema_in_its_sql() -> None:
    """M12 §3.1, §9: no SQL in M12's code or migration names another module's schema."""
    others = {"identity", "catalog", "subscriptions", "orders", "billing", "hub_ops", "logistics"}
    others |= {"partners", "community", "intel", "notifications", "assistant", "platform"}
    qualified = re.compile(r"\b([a-z_]+)\.[a-z_]+\b")
    for path in [*SRC.glob("*.py"), MIGRATION]:
        for line in path.read_text().splitlines():
            if line.lstrip().startswith(("from ", "import ")) or not any(
                word in line.upper() for word in ("SELECT", "FROM", "JOIN", "REFRESH")
            ):
                continue
            named = {m.group(1) for m in qualified.finditer(line)} & others
            assert not named, f"{path.name}: {line.strip()}"


def test_no_fact_table_has_a_personal_column() -> None:
    """M12 §9: a schema walk of `analytics` finds no name, phone, flat or address column."""
    personal = re.compile(r"name|phone|flat|address|tower|email|health|photo|note")
    tables = [t for t in Base.metadata.tables.values() if t.schema == models.SCHEMA]
    assert len(tables) == 13
    for table in tables:
        for column in table.columns:
            assert not personal.search(column.name), f"{table.name}.{column.name}"


def test_money_is_bigint_paise() -> None:
    tables = [t for t in Base.metadata.tables.values() if t.schema == models.SCHEMA]
    paise = [c for t in tables for c in t.columns if c.name.endswith("_paise")]
    assert paise and all(str(c.type) == "BIGINT" for c in paise)
