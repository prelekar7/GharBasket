"""The `platform.environment` mark (ADR-0036): migrations write APP_ENV into a shared database,
never locally, and a `prod` mark is never replaced. The script guards read it (test_live_guard)."""

import asyncio
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine
from gharbasket.platform.environment import environment_to_mark, mark_environment

from .conftest import alembic_config

NEON = "postgresql://app:pw@ep-x.ap-southeast-1.aws.neon.tech/neondb"
LOCAL = "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket"


def test_a_local_database_is_never_marked() -> None:
    assert environment_to_mark(LOCAL, {"APP_ENV": "prod"}) is None


@pytest.mark.parametrize(
    ("environ", "mark"),
    [
        ({}, "prod"),  # unset is production, as for the API
        ({"APP_ENV": ""}, "prod"),
        ({"APP_ENV": "staging"}, "staging"),
        ({"APP_ENV": " Staging "}, "staging"),
        ({"APP_ENV": "prod"}, "prod"),
    ],
)
def test_a_shared_database_is_marked_from_app_env(environ: dict[str, str], mark: str) -> None:
    assert environment_to_mark(NEON, environ) == mark


def test_an_unknown_app_env_is_refused() -> None:
    with pytest.raises(ValueError, match="APP_ENV=production is not one of"):
        environment_to_mark(NEON, {"APP_ENV": "production"})


@pytest.mark.anyio
@pytest.mark.services
async def test_a_prod_mark_is_never_replaced(db_session: AsyncSession) -> None:
    connection = await db_session.connection()
    marks = [
        await connection.run_sync(lambda sync, name=name: mark_environment(sync, name))
        for name in ("staging", "dev", "prod", "staging", "dev")
    ]

    assert marks == ["staging", "dev", "prod", "prod", "prod"]
    assert await connection.scalar(text("SELECT count(*) FROM platform.environment")) == 1


@pytest.mark.anyio
@pytest.mark.services
async def test_nothing_is_marked_before_the_table_exists(db_session: AsyncSession) -> None:
    connection = await db_session.connection()
    await connection.execute(text("ALTER TABLE platform.environment RENAME TO gone"))

    assert await connection.run_sync(lambda sync: mark_environment(sync, "prod")) is None


def _mark_of(database_url: str) -> str | None:
    async def query() -> str | None:
        engine = create_engine(database_url, pooled=False)
        try:
            async with engine.connect() as connection:
                return await connection.scalar(text("SELECT name FROM platform.environment"))
        finally:
            await engine.dispose()

    return asyncio.run(query())


def _unmark(database_url: str) -> None:
    async def delete() -> None:
        engine = create_engine(database_url, pooled=False)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM platform.environment"))
        finally:
            await engine.dispose()

    asyncio.run(delete())


@pytest.fixture
def shared_url(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The test database as migrations see a shared one (0.0.0.0 is not on the local list, as in
    test_hardening), with the deploy flag; its mark is removed afterwards."""
    monkeypatch.setenv("GB_ALLOW_REMOTE_MIGRATIONS", "1")
    try:
        yield database_url.replace("localhost", "0.0.0.0")
    finally:
        _unmark(database_url)


@pytest.mark.services
def test_an_upgrade_of_a_shared_database_writes_app_env(
    shared_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    command.upgrade(alembic_config(shared_url), "head")  # at head already: only the mark
    assert _mark_of(database_url) == "staging"

    monkeypatch.delenv("APP_ENV")
    command.upgrade(alembic_config(shared_url), "head")
    assert _mark_of(database_url) == "prod"

    monkeypatch.setenv("APP_ENV", "staging")
    command.upgrade(alembic_config(shared_url), "head")
    assert _mark_of(database_url) == "prod"  # a staging deploy pointed at production


@pytest.mark.services
def test_an_unknown_app_env_stops_the_migration_and_writes_nothing(
    shared_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(SystemExit, match="Refusing to migrate: APP_ENV=production"):
        command.upgrade(alembic_config(shared_url), "head")

    assert _mark_of(database_url) is None


@pytest.mark.services
def test_an_upgrade_of_the_local_database_writes_no_mark(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")

    command.upgrade(alembic_config(database_url), "head")

    assert _mark_of(database_url) is None
