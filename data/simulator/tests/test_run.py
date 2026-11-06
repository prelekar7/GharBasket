from datetime import date
from pathlib import Path

import pytest
from gharbasket_sim import run as sim
from gharbasket_sim import truth
from gharbasket_sim.catalog import Catalog, load_catalog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.catalog.models import Price, PriceList
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
    # October 2026 in the generated calendar: Indira Ekadashi, nine days of Navratri, Dussehra,
    # Papankusha Ekadashi, Sankashti (data/external/festivals).
    assert [f.code for f in loaded.festivals] == [
        "ekadashi",
        *(["navratri"] * 9),
        "dussehra",
        "ekadashi",
        "sankashti",
    ]

    def without_cost(recipes: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "cost_per_serving_paise"} for r in recipes]

    assert without_cost(loaded.recipes) == without_cost(expected.recipes)
    for got, want in zip(loaded.recipes, expected.recipes, strict=True):
        assert abs(got["cost_per_serving_paise"] - want["cost_per_serving_paise"]) <= 1


async def test_prices_come_from_the_start_s_list_else_today_s(db_session: AsyncSession) -> None:
    """The start's list wins; a product it lacks, or a run that starts before the hub's first
    list, takes today's list (what M3 and M4 charge), so a staging load 12 weeks back is priced."""
    await seeded(db_session)  # the seed's one list is valid from 2026-10-01
    files = json_catalog()
    ids = await catalog_api.product_ids_by_slug(db_session, sorted(files.products))
    in_recipes = {i.product for items in files.ingredients.values() for i in items}
    priced = sorted(s for s, p in files.products.items() if p.price_paise)
    changed = priced[0]
    dropped = next(s for s in priced if s not in in_recipes)  # recipes need every ingredient priced
    seed_list = (await db_session.execute(select(PriceList))).scalar_one()
    newer = PriceList(hub_id=SEED_HUB_ID, valid_from=date(2026, 10, 20), data_source="synthetic")
    db_session.add(newer)
    await db_session.flush()
    for price in (await db_session.execute(select(Price))).scalars().all():
        if price.price_list_id == seed_list.id and price.product_id != ids[dropped]:
            bump = 100 if price.product_id == ids[changed] else 0
            db_session.add(
                Price(
                    price_list_id=newer.id,
                    product_id=price.product_id,
                    price_paise=price.price_paise + bump,
                )
            )
    await db_session.flush()
    seed_price = files.products[changed].price_paise
    assert seed_price is not None

    at_start = await load_catalog(db_session, SEED_HUB_ID, date(2026, 10, 5), date(2026, 10, 18))
    before = await load_catalog(db_session, SEED_HUB_ID, date(2026, 8, 10), date(2026, 9, 27))

    assert at_start.products == files.products  # the seed list is in force at the start
    assert before.products[changed].price_paise == seed_price + 100  # today's list
    assert before.products[dropped].price_paise is None  # today's list does not sell it


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


NEON = "postgresql+asyncpg://u:secret@ep-x.neon.tech/gharbasket"


class Reached(Exception):
    """The CLI got past its guard and would have connected."""


@pytest.mark.parametrize(
    ("argv", "confirmation"),
    [
        (["--allow-remote"], None),
        (["--allow-remote"], "ep-other.neon.tech"),
        ([], "ep-x.neon.tech"),
    ],
)
def test_the_cli_needs_the_flag_and_the_typed_host_for_a_shared_database(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    confirmation: str | None,
) -> None:
    monkeypatch.setenv("DATABASE_URL_DIRECT", NEON)
    monkeypatch.delenv("GB_ALLOW_REMOTE_SEED", raising=False)
    if confirmation is not None:
        monkeypatch.setenv("GB_ALLOW_REMOTE_SEED", confirmation)

    with pytest.raises(SystemExit) as exited:
        sim.main(["--weeks", "1", "--write-db", *argv])

    assert exited.value.code == 2
    assert "secret" not in capsys.readouterr().err


def test_the_cli_writes_to_a_shared_database_with_both_confirmations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL_DIRECT", NEON)
    monkeypatch.setenv("GB_ALLOW_REMOTE_SEED", "ep-x.neon.tech")

    async def connect(url: str, args: object) -> None:
        raise Reached(url)

    monkeypatch.setattr(sim, "run", connect)
    with pytest.raises(Reached):
        sim.main(["--weeks", "1", "--write-db", "--allow-remote"])

    assert "simulating into the shared database at ep-x.neon.tech" in capsys.readouterr().out


def test_a_real_account_on_a_simulator_phone_is_a_one_line_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL_DIRECT", NEON)
    monkeypatch.setenv("GB_ALLOW_REMOTE_SEED", "ep-x.neon.tech")

    async def refuse(url: str, args: object) -> None:
        raise sim.RealUserOnSimulatorPhone("+919800100004 is a real account on a shared database")

    monkeypatch.setattr(sim, "run", refuse)
    with pytest.raises(SystemExit) as exited:
        sim.main(["--weeks", "1", "--write-db", "--allow-remote"])

    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "error: +919800100004 is a real account on a shared database" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(("argv", "places"), [([], True), (["--no-uncommitted"], False)])
def test_the_cli_places_the_add_ons_unless_told_not_to(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], places: bool
) -> None:
    monkeypatch.setenv("DATABASE_URL_DIRECT", "postgresql+asyncpg://u:p@localhost:5432/x")
    seen: dict[str, object] = {}

    async def simulate(session: object, **kwargs: object) -> None:
        seen.update(kwargs)
        raise Reached("simulated")

    monkeypatch.setattr(sim, "simulate_world", simulate)
    with pytest.raises(Reached):
        sim.main(["--weeks", "1", "--write-db", *argv])

    assert seen["place_uncommitted"] is places
