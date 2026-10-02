import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text

from gharbasket.main import create_app
from gharbasket.platform.db import DbSession, create_engine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings
from gharbasket.platform.valkey import create_valkey

API_DIR = Path(__file__).resolve().parents[1]
# Local Docker defaults (`just up`); CI sets the same variables.
LOCAL_TEST_DATABASE_URL = (
    "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket_test"
)
LOCAL_TEST_VALKEY_URL = "redis://localhost:6380/15"


def make_settings(**overrides: object) -> Settings:
    """Settings for tests: never read a developer's .env file."""
    values: dict[str, object] = {"app_env": "test", "log_json": True, **overrides}
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def add_test_routes(app: FastAPI) -> None:
    @app.get("/v1/_test/problem")
    async def raise_problem() -> None:
        raise ProblemError(409, "orders.cutoff_passed", "Orders for this slot are already locked.")

    @app.get("/v1/_test/boom")
    async def raise_unexpected() -> None:
        raise RuntimeError("database password is hunter2")

    class Profile(BaseModel):
        name: str
        age: int

    @app.post("/v1/_test/profile")
    async def save_profile(profile: Profile) -> Profile:
        return profile

    @app.get("/v1/_test/db")
    async def read_from_database(session: DbSession) -> int:
        return (await session.execute(text("SELECT 41 + 1"))).scalar_one()


def _unavailable(reason: str) -> NoReturn:
    """Without Docker, tests that need Postgres or Valkey are skipped; CI sets
    GB_REQUIRE_SERVICES=1 so they fail there instead of silently skipping."""
    if os.environ.get("GB_REQUIRE_SERVICES") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def alembic_config(database_url: str) -> Config:
    return Config(toml_file=API_DIR / "pyproject.toml", attributes={"database_url": database_url})


@pytest.fixture(scope="session")
def database_url() -> str:
    """The test database, migrated to the latest revision."""
    url = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)

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
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def valkey_url() -> str:
    url = os.environ.get("TEST_VALKEY_URL", LOCAL_TEST_VALKEY_URL)

    async def probe() -> None:
        client = create_valkey(url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(probe())
    except Exception:
        _unavailable("Valkey is not reachable at TEST_VALKEY_URL (run `just up`)")
    return url


@pytest.fixture
def app() -> FastAPI:
    app = create_app(make_settings(cors_allow_origins=["https://localhost"]))
    add_test_routes(app)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
