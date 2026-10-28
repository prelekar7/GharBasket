"""The seed catalog for test fixtures, built once per process instead of once per test.

`seed_catalog` is about 2,000 statements (91 products with their search terms, 120 recipes, prices,
festival days): over two seconds per test that used the `world` fixtures, a fifth of the suite's
time. It always writes the same rows, so the first call per process runs the real function and
keeps what it wrote; later calls insert those rows back, one statement per table, inside the
test's own transaction (so the rollback still removes them). The rows are identical, ids and
timestamps included.

Only fixtures use this. Tests of the seed itself (tests/catalog/test_seed_catalog.py ...) call the
real `seed_catalog`. A call on a database whose catalog is not empty also runs the real function,
whose rule is to add what is missing.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import seed_catalog as seed_catalog_for_real

SCHEMA = "catalog"

# Parents before children. A table the seed does not fill (tiffin_menus) is not listed.
TABLES = (
    "categories",
    "products",
    "search_terms",
    "price_lists",
    "prices",
    "recipes",
    "recipe_ingredients",
    "festival_calendar",
)

_cache: dict[str, Any] | None = None


async def _catalog_is_empty(session: AsyncSession) -> bool:
    return not (
        await session.execute(
            text(
                f"SELECT EXISTS (SELECT 1 FROM {SCHEMA}.categories) "
                f"OR EXISTS (SELECT 1 FROM {SCHEMA}.products) "
                f"OR EXISTS (SELECT 1 FROM {SCHEMA}.recipes) "
                f"OR EXISTS (SELECT 1 FROM {SCHEMA}.festival_calendar)"
            )
        )
    ).scalar_one()


async def _writable_columns(session: AsyncSession, table: str) -> str:
    """The columns an INSERT may name: not the generated ones (search_terms.term_norm)."""
    columns = (
        (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table "
                    "AND is_generated = 'NEVER' ORDER BY ordinal_position"
                ),
                {"schema": SCHEMA, "table": table},
            )
        )
        .scalars()
        .all()
    )
    return ", ".join(f'"{column}"' for column in columns)


async def seed_catalog(session: AsyncSession) -> list[tuple[str, bool]]:
    """`gharbasket.tools.seed.seed_catalog` with its default arguments, and its result."""
    global _cache
    if not await _catalog_is_empty(session):
        return await seed_catalog_for_real(session)
    if _cache is None:
        saved = await seed_catalog_for_real(session)
        tables: dict[str, tuple[str, str]] = {}
        for table in TABLES:
            rows = (
                await session.execute(
                    text(f"SELECT coalesce(jsonb_agg(to_jsonb(t)), '[]') FROM {SCHEMA}.{table} t")
                )
            ).scalar_one()
            tables[table] = (await _writable_columns(session, table), json.dumps(rows))
        _cache = {"saved": saved, "tables": tables}
        return saved
    for table, (columns, rows) in _cache["tables"].items():
        await session.execute(
            text(
                f"INSERT INTO {SCHEMA}.{table} ({columns}) "
                f"SELECT {columns} FROM "
                f"jsonb_populate_recordset(NULL::{SCHEMA}.{table}, CAST(:rows AS jsonb))"
            ),
            {"rows": rows},
        )
    return list(_cache["saved"])
