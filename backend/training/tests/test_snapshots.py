"""The snapshot export (no personal data, typed columns) and the point-in-time features with
their leakage test (eval protocol §3)."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pyarrow as pa
import pytest
from gharbasket_sim.run import simulate_world
from gharbasket_training import features, snapshots
from gharbasket_training.lake import LocalLake
from gharbasket_training.settings import Settings
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

pytestmark = [pytest.mark.anyio, pytest.mark.services]

START = date(2026, 10, 5)  # Monday; the seed delivers on Tuesdays (6 and 13 Oct)


async def two_weeks(db_session: AsyncSession) -> None:
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)
    await simulate_world(
        db_session, seed=7, weeks=2, start=START, planner="rules", adoption=0.02, write_db=True
    )


def test_the_schema_check_refuses_a_wrong_type() -> None:
    table = snapshots.TABLES[0]  # households: size must be an integer
    row: list[Any] = [None] * len(table.columns)
    row[table.columns.index("size")] = "four"
    row[table.columns.index("id")] = uuid.uuid4()
    with pytest.raises(TypeError, match=r"households\.size"):
        snapshots.to_table(table, [row])
    empty = snapshots.to_table(table, [])
    assert empty.num_rows == 0 and empty.schema.field("size").type == pa.int64()


async def test_the_export_writes_typed_parquet_without_personal_data(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await two_weeks(db_session)
    lake = LocalLake(tmp_path)

    counts = await snapshots.export(db_session, lake, date(2026, 10, 14))

    assert counts["households"] > 0 and counts["weekly_cycles"] > 0 and counts["box_lines"] > 0
    assert counts["orders"] > 0 and counts["products"] > 0 and counts["prices"] > 0
    households = lake.read("snapshots/date=2026-10-14/households.parquet")
    assert households is not None
    assert not {"owner_user_id", "phone", "name", "flat", "tower_id"} & set(households.column_names)
    assert households.schema.field("size").type == pa.int64()
    assert set(households.column("data_source").to_pylist()) <= {"real", "synthetic"}
    cycles = lake.read("snapshots/date=2026-10-14/weekly_cycles.parquet")
    assert cycles is not None and set(cycles.column("status").to_pylist()) <= {
        "locked",
        "skipped",
        "cancelled",
        "approved",
        "awaiting_approval",
    }
    assert sorted(lake.keys("snapshots/date=2026-10-14"))[0].endswith("box_lines.parquet")


async def test_features_see_only_what_came_before_the_cut_off(db_session: AsyncSession) -> None:
    await two_weeks(db_session)
    before_first = datetime(2026, 10, 1, tzinfo=UTC)
    between = datetime(2026, 10, 9, tzinfo=UTC)  # after the 6 Oct box, before the 13 Oct one
    after_both = datetime(2026, 10, 20, tzinfo=UTC)

    nothing = await features.build(db_session, before_first)
    one = await features.build(db_session, between)
    two = await features.build(db_session, after_both)

    assert nothing == {}
    assert one and all(f["cycles"] == 1 and f["weeks_observed"] <= 1 for f in one.values())
    assert all(f["cycles"] == 2 for f in two.values()) and set(one) <= set(two)
    locked = next((f for f in two.values() if f["weeks_observed"] == 2), None)
    assert locked is not None and locked["weekly_use_milk"] > 0
    assert all(f["as_of"] == between.isoformat() for f in one.values())
    # Nothing in a feature row depends on rows after the cut-off: the same cut-off again gives
    # the same features after the second week has happened (the data above already has it).
    assert await features.build(db_session, between) == one


async def test_features_are_pushed_through_the_api() -> None:
    received: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        received["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"accepted": len(received["body"]["items"])})

    as_of = datetime(2026, 10, 9, tzinfo=UTC)
    settings = Settings(api_url="https://api.test", jobs_token=SecretStr("t"))
    built = {uuid.uuid4(): {"weeks_observed": 1}}
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        assert await features.push(http, settings, as_of, built) == 1
    assert received["body"]["as_of"] == as_of.isoformat()
    assert received["body"]["items"][0]["subject_type"] == "household"
    assert (datetime.fromisoformat(received["body"]["as_of"]) - as_of) == timedelta(0)
