"""The snapshot export (no personal data, one Arrow schema per table, day partitions that hold only
earlier rows), the point-in-time features with their leakage test (eval protocol §3) and the
job's IST day and cut-off.

The simulator stamps rows with the database clock, so `two_weeks` overwrites every timestamp the
export or the features read with explicit values: nothing here depends on the day it runs.
"""

import argparse
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from gharbasket_sim.run import simulate_world
from gharbasket_training import features, snapshots
from gharbasket_training.jobs import export_snapshots
from gharbasket_training.lake import LocalLake
from gharbasket_training.settings import Settings
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.intel_gateway.models import PriceIndex
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    EditFeedback,
    PantryFeedback,
    WeeklyCycle,
)
from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

pytestmark = [pytest.mark.anyio, pytest.mark.services]

IST = ZoneInfo("Asia/Kolkata")
START = date(2026, 10, 5)  # Monday; the seed delivers on Tuesdays (6 and 13 Oct)
SET_UP = datetime(2026, 9, 1, 10, 0, tzinfo=IST)  # households, products and price lists
# The cut-off the 02:03 IST run of 10 Oct uses: 21:00 IST on 9 Oct. The first box (6 Oct) is
# before it; the second (generated 10 Oct, delivered 13 Oct) after it.
AS_OF = datetime(2026, 10, 9, 21, 0, tzinfo=IST).astimezone(UTC)
STAMPS = (
    "UPDATE identity.households SET created_at = :set_up",
    "UPDATE catalog.products SET created_at = :set_up",
    "UPDATE catalog.price_lists SET created_at = :set_up",
    # Generated T-3 at 18:00 IST; edited the day before the cut-off; tapped the day after delivery.
    "UPDATE subscriptions.weekly_cycles"
    " SET generated_at = (delivery_date - 3 + time '18:00') AT TIME ZONE 'Asia/Kolkata'",
    "UPDATE subscriptions.edit_feedback e"
    " SET at = (c.delivery_date - 2 + time '12:00') AT TIME ZONE 'Asia/Kolkata'"
    " FROM subscriptions.weekly_cycles c WHERE c.id = e.cycle_id",
    "UPDATE subscriptions.pantry_feedback p"
    " SET at = (c.delivery_date + 1 + time '20:00') AT TIME ZONE 'Asia/Kolkata'"
    " FROM subscriptions.weekly_cycles c WHERE c.id = p.cycle_id",
    "UPDATE orders.orders"
    " SET created_at = (delivery_date - 2 + time '09:00') AT TIME ZONE 'Asia/Kolkata'",
    "UPDATE intel.predictions"
    " SET created_at = (for_date - 3 + time '18:00') AT TIME ZONE 'Asia/Kolkata'",
)


async def two_weeks(db_session: AsyncSession) -> None:
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)
    await simulate_world(
        db_session, seed=7, weeks=2, start=START, planner="rules", adoption=0.02, write_db=True
    )
    for statement in STAMPS:
        await db_session.execute(
            text(statement), {"set_up": SET_UP} if ":set_up" in statement else {}
        )
    await db_session.flush()


def test_the_schema_check_refuses_a_wrong_type() -> None:
    table = snapshots.TABLES[0]  # households: size must be an integer
    row: list[Any] = [None] * len(table.columns)
    row[table.column_names.index("size")] = "four"
    row[table.column_names.index("id")] = uuid.uuid4()
    with pytest.raises(TypeError, match=r"households\.size"):
        snapshots.to_table(table, [row])


def test_every_table_has_one_schema_empty_null_or_full() -> None:
    for table in snapshots.TABLES:
        empty = snapshots.to_table(table, [])
        nulls = snapshots.to_table(table, [[None] * len(table.columns)])
        assert empty.num_rows == 0 and nulls.num_rows == 1
        assert empty.schema == nulls.schema == table.schema, table.name
    index = next(t for t in snapshots.TABLES if t.name == "price_index")
    row = [uuid.uuid4(), date(2026, 10, 4), "Vashi", 4200, 1.18, "agmarknet"]
    full = snapshots.to_table(index, [row])  # a float has .hex() too: it must stay a float
    assert full.schema == index.schema and full.column("index_4w").to_pylist() == [1.18]


async def _price_index(db_session: AsyncSession) -> None:
    milk = (await db_session.execute(select(Product.id).where(Product.slug == "milk"))).scalar_one()
    for day, index in ((date(2026, 10, 4), 1.18), (date(2026, 10, 9), 0.92)):
        db_session.add(
            PriceIndex(
                product_id=milk, day=day, market="Vashi", modal_paise_per_kg=4200, index_4w=index
            )
        )
    await db_session.flush()


async def test_the_export_writes_typed_parquet_without_personal_data(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await two_weeks(db_session)
    await _price_index(db_session)
    lake = LocalLake(tmp_path)

    counts = await snapshots.export(db_session, lake, date(2026, 10, 14))
    before = await snapshots.export(db_session, lake, date(2026, 8, 31))  # before anything

    assert counts["households"] > 0 and counts["weekly_cycles"] > 0 and counts["box_lines"] > 0
    assert counts["orders"] > 0 and counts["products"] > 0 and counts["prices"] > 0
    assert counts["edit_feedback"] > 0 and counts["pantry_feedback"] > 0
    assert counts["price_index"] == 2 and set(before.values()) == {0}
    for table in snapshots.TABLES:  # one schema, empty or full, through Parquet
        full = lake.read(f"snapshots/date=2026-10-14/{table.name}.parquet")
        empty = lake.read(f"snapshots/date=2026-08-31/{table.name}.parquet")
        assert full is not None and empty is not None
        assert full.schema == empty.schema == table.schema, table.name
        assert "society_id" not in full.column_names and "budget_paise" not in full.column_names
    households = lake.read("snapshots/date=2026-10-14/households.parquet")
    assert households is not None
    assert not {"owner_user_id", "phone", "name", "flat", "tower_id"} & set(households.column_names)
    assert set(households.column("budget_band").to_pylist()) <= {
        None,
        *(label for _, label in snapshots.BUDGET_BANDS),
        "12k_plus",
    }
    assert set(households.column("data_source").to_pylist()) <= {"real", "synthetic"}
    index = lake.read("snapshots/date=2026-10-14/price_index.parquet")
    assert index is not None and sorted(index.column("index_4w").to_pylist()) == [0.92, 1.18]
    cycles = lake.read("snapshots/date=2026-10-14/weekly_cycles.parquet")
    assert cycles is not None and set(cycles.column("status").to_pylist()) <= {
        "locked",
        "skipped",
        "cancelled",
        "approved",
        "awaiting_approval",
    }
    assert sorted(lake.keys("snapshots/date=2026-10-14"))[0].endswith("box_lines.parquet")


async def test_a_backfilled_day_holds_only_rows_from_before_its_end(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await two_weeks(db_session)
    await _price_index(db_session)
    lake = LocalLake(tmp_path)

    # 8 Oct: the 6 Oct box and its taps (7 Oct), not the 13 Oct box (generated 10 Oct).
    counts = await snapshots.export(db_session, lake, date(2026, 10, 8))

    def column(table: str, name: str) -> list[Any]:
        arrow = lake.read(f"snapshots/date=2026-10-08/{table}.parquet")
        assert arrow is not None
        return arrow.column(name).to_pylist()

    first = {date(2026, 10, 6)}
    assert counts["weekly_cycles"] > 0 and set(column("weekly_cycles", "delivery_date")) == first
    assert set(column("box_lines", "delivery_date")) == first
    assert set(column("orders", "delivery_date")) <= first
    assert set(column("order_lines", "delivery_date")) <= first
    assert set(column("edit_feedback", "delivery_date")) <= first
    assert set(column("pantry_feedback", "delivery_date")) <= first
    end = snapshots.end_of(date(2026, 10, 8))
    assert end == datetime(2026, 10, 9, tzinfo=IST)
    assert all(at < end for at in column("pantry_feedback", "at"))
    assert column("price_index", "day") == [date(2026, 10, 4)]  # not the 9 Oct price


async def _late_rows(
    db_session: AsyncSession, cycle: WeeklyCycle, at: datetime, delivered: date, products: list
) -> None:
    """Rows stamped `at` that would change `cycle`'s household's features if they were seen:
    an edit, a pantry tap, and a locked box (delivered before the cut-off) generated at `at`."""
    db_session.add(EditFeedback(cycle_id=cycle.id, product_id=products[0], action="qty_up", at=at))
    db_session.add(
        PantryFeedback(
            cycle_id=cycle.id,
            household_id=cycle.household_id,
            product_id=products.pop(),
            signal="ran_out",
            at=at,
        )
    )
    late = WeeklyCycle(
        household_id=cycle.household_id,
        hub_id=cycle.hub_id,
        society_id=cycle.society_id,
        subscription_id=cycle.subscription_id,
        week_start=delivered - timedelta(days=1),
        delivery_date=delivered,
        slot_id=cycle.slot_id,
        cutoff_at=datetime.combine(delivered - timedelta(days=1), time(21), IST),
        plan_source="fallback",
        status="locked",
        generated_at=at,
        data_source=cycle.data_source,
    )
    db_session.add(late)
    await db_session.flush()
    milk = (await db_session.execute(select(Product).where(Product.slug == "milk"))).scalar_one()
    db_session.add(
        BoxLine(
            cycle_id=late.id,
            product_id=milk.id,
            qty=99,
            step_qty=milk.step_qty,
            step_unit=milk.step_unit,
            unit_price_paise=1,
            reason_code="daily_use",
            source="rule",
        )
    )
    await db_session.flush()


async def test_features_see_only_what_came_before_the_cut_off(db_session: AsyncSession) -> None:
    await two_weeks(db_session)
    before_first = datetime(2026, 10, 1, tzinfo=IST)
    after_both = datetime(2026, 10, 20, tzinfo=IST)

    nothing = await features.build(db_session, before_first)
    one = await features.build(db_session, AS_OF)
    two = await features.build(db_session, after_both)

    assert nothing == {}
    assert one and all(f["cycles"] == 1 and f["weeks_observed"] <= 1 for f in one.values())
    assert all(f["cycles"] == 2 for f in two.values()) and set(one) <= set(two)
    locked = next((f for f in two.values() if f["weeks_observed"] == 2), None)
    assert locked is not None and locked["weekly_use_milk"] > 0
    assert all(f["as_of"] == AS_OF.isoformat() for f in one.values())

    # Rows exactly at the cut-off and one second after it, for a household seen before it.
    cycle = (
        await db_session.execute(
            select(WeeklyCycle)
            .where(WeeklyCycle.delivery_date == date(2026, 10, 6), WeeklyCycle.status == "locked")
            .order_by(WeeklyCycle.id)
            .limit(1)
        )
    ).scalar_one()
    household = cycle.household_id
    tapped = select(PantryFeedback.product_id).where(PantryFeedback.cycle_id == cycle.id)
    free = list(
        (await db_session.execute(select(Product.id).where(Product.id.not_in(tapped)))).scalars()
    )
    await _late_rows(db_session, cycle, AS_OF, date(2026, 9, 29), free)
    await _late_rows(db_session, cycle, AS_OF + timedelta(seconds=1), date(2026, 9, 22), free)

    # Not seen: the features as of the cut-off are what they were before those rows existed.
    assert await features.build(db_session, AS_OF) == one
    # Seen once the cut-off is past them, so the test above would catch a leak.
    later = (await features.build(db_session, AS_OF + timedelta(seconds=2)))[household]
    assert later["cycles"] == one[household]["cycles"] + 2
    assert later["weeks_observed"] == one[household]["weeks_observed"] + 2
    assert later["taps_ran_out"] == one[household]["taps_ran_out"] + 2
    assert later["weekly_use_milk"] > one[household].get("weekly_use_milk", 0)


async def test_features_are_pushed_through_the_api() -> None:
    received: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        received["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"accepted": len(received["body"]["items"])})

    settings = Settings(api_url="https://api.test", jobs_token=SecretStr("t"))
    built = {uuid.uuid4(): {"weeks_observed": 1}}
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        assert await features.push(http, settings, AS_OF, built) == 1
    assert received["body"]["as_of"] == AS_OF.isoformat()
    assert received["body"]["items"][0]["subject_type"] == "household"
    assert (datetime.fromisoformat(received["body"]["as_of"]) - AS_OF) == timedelta(0)


def test_the_job_takes_the_ist_day_and_the_last_cut_off() -> None:
    nightly = datetime(2026, 10, 9, 20, 33, tzinfo=UTC)  # the cron: 02:03 IST on 10 Oct
    assert export_snapshots.plan(None, nightly) == (date(2026, 10, 10), AS_OF)
    evening = datetime(2026, 10, 9, 21, 30, tzinfo=IST)
    assert export_snapshots.plan(None, evening) == (date(2026, 10, 9), AS_OF)
    assert features.latest_cutoff(AS_OF) == AS_OF
    assert features.latest_cutoff(AS_OF - timedelta(seconds=1)) == AS_OF - timedelta(days=1)
    backfill = export_snapshots.plan(date(2026, 10, 5), nightly)
    assert backfill == (date(2026, 10, 5), datetime(2026, 10, 5, 21, tzinfo=IST))
    with pytest.raises(ValueError, match="future"):
        export_snapshots.plan(date(2026, 10, 11), nightly)


async def test_the_job_reads_one_read_only_snapshot(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}
    export = snapshots.export

    async def spy(session: AsyncSession, lake: Any, day: date) -> dict[str, int]:
        for setting in ("transaction_isolation", "transaction_read_only"):
            seen[setting] = (await session.execute(text(f"SHOW {setting}"))).scalar_one()
        return await export(session, lake, day)

    monkeypatch.setattr(snapshots, "export", spy)
    settings = Settings(database_url=SecretStr(database_url))
    args = argparse.Namespace(day=None, out=tmp_path, no_features=False, push_features=False)

    counts = await export_snapshots.run(settings, args, datetime(2026, 10, 9, 20, 33, tzinfo=UTC))

    assert seen == {"transaction_isolation": "repeatable read", "transaction_read_only": "on"}
    assert "feature_snapshots" in counts
    assert set(LocalLake(tmp_path).keys("snapshots/date=2026-10-10")) == {
        f"snapshots/date=2026-10-10/{t.name}.parquet" for t in snapshots.TABLES
    }
