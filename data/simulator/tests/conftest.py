import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from gharbasket_sim.catalog import (
    PERISHABLE_H,
    Catalog,
    Festival,
    Ingredient,
    Product,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.recipes import BASE_UNIT
from gharbasket.platform.db import create_engine, is_local_database

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "data" / "seeds" / "catalog"
LOCAL_TEST_DATABASE_URL = (
    "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket_test"
)


def json_catalog() -> Catalog:
    """The seed catalog read straight from data/seeds, as `load_catalog` reads it from the
    database after `just seed` (test_run checks the two agree)."""
    raw = json.loads((SEED / "products.json").read_text())["products"]
    steps = {p["slug"]: p["step"]["qty"] * BASE_UNIT[p["step"]["unit"]][1] for p in raw}
    shelf = {p["slug"]: p["shelf_life_h"] for p in raw}
    prices = {
        i["slug"]: i["price_paise"] for i in json.loads((SEED / "prices.json").read_text())["items"]
    }
    unit_g = {
        n["slug"]: n.get("unit_g")
        for n in json.loads((SEED / "nutrition.json").read_text())["items"]
    }
    products = {
        p["slug"]: Product(
            p["slug"],
            BASE_UNIT[p["step"]["unit"]][0],
            p["kind"],
            frozenset(p["tags"]),
            steps[p["slug"]],
            prices.get(p["slug"]),
            shelf[p["slug"]],
            p["category"],
            unit_g.get(p["slug"]),
        )
        for p in raw
    }
    recipes, ingredients = [], {}
    for r in sorted(
        json.loads((SEED / "recipes.json").read_text())["recipes"], key=lambda r: r["slug"]
    ):
        if r.get("is_side"):
            continue
        tags = set().union(*(products[i["product"]].tags for i in r["ingredients"]))
        no_onion_garlic = "onion_garlic" not in tags
        diet = "egg" if "egg" in tags else "veg"
        recipes.append(
            {
                "slug": r["slug"],
                "cuisine": r["cuisine"],
                "slots": r["slots"],
                "cook_minutes": r["cook_minutes"],
                "diet": diet,
                "jain_ok": no_onion_garlic and "root" not in tags and diet == "veg",
                "no_onion_garlic": no_onion_garlic,
                "fasting_ok": r["fasting_ok"],
                "diabetic_ok": r["diabetic_ok"],
                "no_cook": "no_cook" in r["tags"],
                "cost_per_serving_paise": round(
                    sum(
                        i["qty"] / steps[i["product"]] * prices[i["product"]]
                        for i in r["ingredients"]
                    )
                ),
                "perishables": sorted(
                    i["product"] for i in r["ingredients"] if shelf[i["product"]] <= PERISHABLE_H
                ),
            }
        )
        ingredients[r["slug"]] = tuple(
            sorted(
                Ingredient(i["product"], float(i["qty"]), i["optional"]) for i in r["ingredients"]
            )
        )
    festivals = [
        Festival(date.fromisoformat(f["date"]), f["code"], f["rules"])
        for f in json.loads((SEED / "festivals.json").read_text())["festivals"]
    ]
    return Catalog(products, recipes, ingredients, festivals)


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return json_catalog()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _unavailable(reason: str) -> NoReturn:
    # CI sets GB_REQUIRE_SERVICES=1 so these fail there instead of silently skipping.
    if os.environ.get("GB_REQUIRE_SERVICES") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def database_url() -> str:
    """The API's test database (never Neon), migrated to the latest revision."""
    url = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)
    if not is_local_database(url):
        pytest.fail("TEST_DATABASE_URL must point at the local Docker database, never at Neon")

    async def probe() -> None:
        engine = create_engine(url, pooled=False)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(probe())
    except Exception:
        _unavailable("Postgres is not reachable at TEST_DATABASE_URL (run `just up`)")
    api = REPO / "backend" / "api"
    command.upgrade(
        AlembicConfig(toml_file=api / "pyproject.toml", attributes={"database_url": url}), "head"
    )
    return url


@pytest.fixture
async def db_session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is rolled back after the test (as in backend/api)."""
    engine = create_engine(database_url, pooled=False)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()
