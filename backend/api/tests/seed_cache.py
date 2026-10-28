"""The seed functions for test fixtures, run for real once per process and replayed after that.

A `world` fixture seeds people, societies, households, the catalog ... about 3,000 statements,
two to four seconds on a developer machine and ten on a CI runner: over half of the suite's time,
as 700 tests start from one. Every run writes the same rows. So the first call of a seed function
in a process runs the real one inside the test's transaction and keeps the rows it wrote; a later
call inserts those rows back, one statement per table, inside its own transaction (so the rollback
still removes them): ids and timestamps are the first run's. A call is replayed only if the
database holds exactly what it held when the rows were kept (a hash of every table), so a test that
wrote something first, or ran another seed first, gets the real function and the rows it would
make there, never a wrong guess. The clock is part of the key: seeds read it (a test may travel in
time), so one clock hour has one set of rows.

Only fixtures import these. Tests of the seed itself (tests/test_seed.py, tests/catalog/
test_seed_catalog.py ...) and test bodies that call a seed to see what it does import the real
functions from `gharbasket.tools.seed`.
"""

import functools
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Concatenate

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools import seed as real_seeds


@dataclass(frozen=True)
class Kept:
    changed: Mapping[str, list[str]]  # the tables it changed: their rows, after it ran
    columns: Mapping[str, str]  # the columns an INSERT names (not the generated ones)
    had_rows: frozenset[str]  # changed tables that held rows before: emptied before the insert
    result: Any


_tables: list[str] | None = None
_can_replay: bool | None = None
_kept: dict[tuple[object, ...], Kept] = {}


async def _table_names(session: AsyncSession) -> list[str]:
    global _tables
    if _tables is None:
        rows = await session.execute(
            text(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'public') "
                "AND n.nspname NOT LIKE 'pg_%' ORDER BY 1"
            )
        )
        _tables = list(rows.scalars())
    return _tables


async def _hashes(session: AsyncSession) -> dict[str, str]:
    """A hash of each table's rows: equal hashes, equal content (ids and timestamps too)."""
    selects = " UNION ALL ".join(
        f"SELECT '{table}', md5(coalesce(string_agg(t::text, ',' ORDER BY t::text), '')) "
        f"FROM {table} t"
        for table in await _table_names(session)
    )
    return {name: digest for name, digest in (await session.execute(text(selects))).all()}


async def _rows(session: AsyncSession, table: str) -> list[str]:
    """The rows of `table` in Postgres' own text form, which reads back as the same row (a JSON
    null stays one, unlike through JSON)."""
    sql = f"SELECT coalesce(array_agg(t::text), '{{}}') FROM {table} t"
    return list((await session.execute(text(sql))).scalar_one())


async def _insert_columns(session: AsyncSession, table: str) -> str:
    schema, name = table.split(".")
    found = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :name AND is_generated = 'NEVER' "
            "ORDER BY ordinal_position"
        ),
        {"schema": schema, "name": name},
    )
    return ", ".join(f'"{column}"' for column in found.scalars())


async def _may_replay(session: AsyncSession) -> bool:
    """Replaying inserts rows with the foreign keys off, which needs a superuser (the test
    database's owner is one); without it the real seeds run."""
    global _can_replay
    if _can_replay is None:
        sql = "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        _can_replay = bool((await session.execute(text(sql))).scalar_one())
    return _can_replay


async def _replay(session: AsyncSession, kept: Kept) -> None:
    # The kept rows are consistent; checking their foreign keys again is what costs the time.
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    try:
        for table, rows in kept.changed.items():
            if table in kept.had_rows:
                await session.execute(text(f"DELETE FROM {table}"))
            columns = kept.columns[table]
            await session.execute(
                text(
                    f"INSERT INTO {table} ({columns}) OVERRIDING SYSTEM VALUE "
                    f"SELECT {columns} FROM "
                    f"(SELECT (r::{table}).* FROM unnest(CAST(:rows AS text[])) r) x"
                ),
                {"rows": rows},
            )
    finally:
        await session.execute(text("SET LOCAL session_replication_role = origin"))


def cached[**P, R](
    seed: Callable[Concatenate[AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[AsyncSession, P], Awaitable[R]]:
    """`seed`, replaying the rows of its first run where the database is as that run found it."""

    @functools.wraps(seed)
    async def run(session: AsyncSession, /, *args: P.args, **kwargs: P.kwargs) -> R:
        if not await _may_replay(session):
            return await seed(session, *args, **kwargs)
        hour = datetime.now(UTC).strftime("%Y%m%d%H")
        before = await _hashes(session)
        # What the database holds is part of the key: `seed_hubs` runs after `seed` in one
        # fixture and after the whole world in another, which are two sets of rows.
        key = (
            seed.__qualname__,
            args,
            tuple(sorted(kwargs.items())),
            hour,
            tuple(sorted(before.items())),
        )
        kept = _kept.get(key)
        if kept is not None:
            await _replay(session, kept)
            return kept.result
        tables = await _table_names(session)
        rows_before = {table: await _rows(session, table) for table in tables}
        result = await seed(session, *args, **kwargs)
        rows_after = {table: await _rows(session, table) for table in tables}
        changed = {t: rows for t, rows in rows_after.items() if rows != rows_before[t]}
        _kept[key] = Kept(
            changed=changed,
            columns={table: await _insert_columns(session, table) for table in changed},
            had_rows=frozenset(t for t in changed if rows_before[t]),
            result=result,
        )
        return result

    return run


seed = cached(real_seeds.seed)
seed_societies = cached(real_seeds.seed_societies)
seed_households = cached(real_seeds.seed_households)
seed_catalog = cached(real_seeds.seed_catalog)
seed_hubs = cached(real_seeds.seed_hubs)
seed_partners = cached(real_seeds.seed_partners)
seed_trainings = cached(real_seeds.seed_trainings)
seed_delivery = cached(real_seeds.seed_delivery)
