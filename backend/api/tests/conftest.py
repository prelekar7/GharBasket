import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import NoReturn

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.db import DbSession, create_engine, get_session, is_local_database
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings
from gharbasket.platform.valkey import create_valkey

from .commit_guard import CommitGuard

API_DIR = Path(__file__).resolve().parents[1]
# Local Docker defaults (`just up`); CI sets the same variables.
LOCAL_TEST_DATABASE_URL = (
    "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket_test"
)
LOCAL_TEST_VALKEY_URL = "redis://localhost:6380/15"


# What staging and prod must have; used by tests that build a deployed configuration.
DEPLOYED_SECRETS: dict[str, object] = {
    "jobs_token": "op-token",
    "jwt_signing_key": "test-deployed-jwt-signing-key-0123456789",
    "refresh_token_pepper": "test-deployed-refresh-pepper-0123456789",
    "firebase_project_id": "gharbasket-test",
}


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
    if not is_local_database(url):
        # Never skip this one: tests migrate and roll back, which must not happen on shared Neon.
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db_session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is rolled back after the test.

    Code under test may call `session.commit()`: with `create_savepoint` that only releases a
    savepoint, so nothing a test writes survives it and tests never see each other's rows.
    """
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


@pytest.fixture
async def commit_guard(db_session: AsyncSession) -> AsyncIterator[CommitGuard]:
    """Fails the test if a request wrote through the session and returned without committing.

    Install it with `app.dependency_overrides[get_session] = commit_guard.override()`.
    """
    guard = CommitGuard(db_session)
    yield guard
    guard.check()


@pytest.fixture
async def api(commit_guard: CommitGuard) -> AsyncIterator[httpx2.AsyncClient]:
    """Async client for the app, with every request using the test's rolled-back session."""
    app = create_app(make_settings())
    add_test_routes(app)
    app.dependency_overrides[get_session] = commit_guard.override()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
