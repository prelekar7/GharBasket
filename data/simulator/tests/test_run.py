from datetime import date
from pathlib import Path

import pytest
from gharbasket_sim import run as sim
from gharbasket_sim import truth
from gharbasket_sim.catalog import Catalog, load_catalog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Household, User
from gharbasket.tools.seed import SEED_HUB_ID, seed_catalog, seed_societies

from .conftest import json_catalog

pytestmark = [pytest.mark.anyio, pytest.mark.services]

START = date(2026, 10, 14)  # a Wednesday: the run starts on that week's Monday


async def seeded(session: AsyncSession) -> None:
    await seed_societies(session)
    await seed_catalog(session)
    await session.flush()


async def test_the_catalog_from_the_database_matches_the_seed_files(
    db_session: AsyncSession,
) -> None:
    await seeded(db_session)

    loaded = await load_catalog(db_session, SEED_HUB_ID, date(2026, 10, 1), date(2026, 10, 31))

    expected = json_catalog()
    assert loaded.products == expected.products
    assert loaded.ingredients == expected.ingredients
    assert [f.code for f in loaded.festivals] == ["navratri"] * 9

    def without_cost(recipes: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "cost_per_serving_paise"} for r in recipes]

    assert without_cost(loaded.recipes) == without_cost(expected.recipes)
    for got, want in zip(loaded.recipes, expected.recipes, strict=True):
        assert abs(got["cost_per_serving_paise"] - want["cost_per_serving_paise"]) <= 1


async def test_households_are_onboarded_once_through_m1(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await seeded(db_session)

    first = await sim.simulate_world(
        db_session, seed=3, weeks=1, start=START, planner="rules", adoption=0.03
    )
    again = await sim.simulate_world(
        db_session, seed=3, weeks=1, start=START, planner="rules", adoption=0.03
    )

    assert first.first_monday == date(2026, 10, 12)
    assert len(first.households) == 26
    assert all(created for _, created in first.created.values())
    assert not any(created for _, created in again.created.values())
    assert {k: i for k, (i, _) in first.created.items()} == {
        k: i for k, (i, _) in again.created.items()
    }
    ids = [i for i, _ in first.created.values()]
    rows = (
        await db_session.execute(
            select(Household.data_source, User.data_source, User.name)
            .join(User, User.id == Household.owner_user_id)
            .where(Household.id.in_(ids))
        )
    ).all()
    assert len(rows) == 26
    assert {(h, u) for h, u, _ in rows} == {("synthetic", "synthetic")}
    assert all(str(name).startswith("Sim h") for _, _, name in rows)

    tables = sim.tables(first)
    truth.write(tmp_path, tables, {"seed": 3, "first_monday": first.first_monday})
    assert truth.read(tmp_path, "weeks") == first.rows.weeks
    assert len(truth.read(tmp_path, "meals")) == 26 * 21
    households = truth.read(tmp_path, "households")
    assert {h["household_id"] for h in households} == {str(i) for i in ids}
    assert (tmp_path / "manifest.json").read_text().count('"meals": 546') == 1
    lines = sim.summary(first)
    assert lines[0] == "26 synthetic households (26 new) across 5 societies"
    assert "planner rules" in lines[1]


async def test_an_empty_catalog_asks_for_just_seed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(*args: object) -> Catalog:
        return Catalog({}, [], {}, [])

    monkeypatch.setattr(sim, "load_catalog", nothing)
    with pytest.raises(LookupError, match="just seed"):
        await sim.simulate_world(db_session, seed=1, weeks=1, start=START, planner="rules")


def test_the_cli_refuses_a_shared_database(monkeypatch: pytest.MonkeyPatch) -> None:
    # The direct URL wins (as for migrations), and it overrides the developer's .env.
    monkeypatch.setenv("DATABASE_URL_DIRECT", "postgresql+asyncpg://u:p@ep-x.neon.tech/gharbasket")

    with pytest.raises(SystemExit):
        sim.main(["--weeks", "1"])
