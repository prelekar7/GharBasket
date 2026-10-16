"""Fixtures shared by the training tests: a rolled-back session on the API's local test database
(as in data/simulator), skipped when Postgres is not running."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine, is_local_database

REPO = Path(__file__).resolve().parents[3]
LOCAL_TEST_DATABASE_URL = (
    "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket_test"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _unavailable(reason: str) -> NoReturn:
    if os.environ.get("GB_REQUIRE_SERVICES") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def database_url() -> str:
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
