import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit, urlunsplit

import httpx2
import pytest
import structlog
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from gharbasket.main import create_app
from gharbasket.modules.registry import load_modules
from gharbasket.platform.db import (
    DbSession,
    asyncpg_url,
    create_engine,
    get_session,
    is_local_database,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings, use_settings
from gharbasket.platform.valkey import create_valkey

from .commit_guard import CommitGuard

API_DIR = Path(__file__).resolve().parents[1]
# As the app and every tool do: tests relay the outbox without always building an app first.
load_modules()


# `create_app` configures structlog with `cache_logger_on_first_use=True`. A module's logger, once
# used, then ignores `structlog.testing.capture_logs`, so a test that captures logs passed or
# failed by what ran before it in the same process: serial and xdist runs order tests differently.
# Tests never cache loggers.
_structlog_configure = structlog.configure


def _configure_structlog_uncached(**settings: Any) -> None:
    _structlog_configure(**{**settings, "cache_logger_on_first_use": False})


structlog.configure = _configure_structlog_uncached

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
    "kyc_encryption_key": "test-deployed-kyc-encryption-key-0123456789",
    "kyc_hash_pepper": "test-deployed-kyc-hash-pepper-0123456789",
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
def worker_id(request: pytest.FixtureRequest) -> str:
    """pytest-xdist's worker id (`gw0`, `gw1` ...), or `master` in a serial run. Defined here as
    xdist defines it, so a run with `-p no:xdist` (the fuzz test alone) still finds it."""
    workerinput = getattr(request.config, "workerinput", None)
    return str(workerinput["workerid"]) if workerinput else "master"


def _worker_number(worker_id: str) -> int | None:
    """0, 1, 2 ... for a pytest-xdist worker (`gw0`, `gw1` ...); None in a plain, serial run."""
    return int(worker_id.removeprefix("gw")) if worker_id.startswith("gw") else None


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


# CREATE and DROP DATABASE can take longer than the app's 15 s statement limit when other test
# runs share the server (the drop waits for the terminated sessions and removes the files), so
# they get a limit of their own instead of failing a test at random.
SERVER_DDL_TIMEOUT = 180


def run_on_server(base_url: str, sql: str) -> None:
    """Run one statement outside any transaction on the server's `postgres` database."""

    async def go() -> None:
        admin_url = (
            make_url(base_url).set(database="postgres").render_as_string(hide_password=False)
        )
        url, connect_args = asyncpg_url(admin_url)
        engine = create_async_engine(
            url,
            connect_args={**connect_args, "command_timeout": SERVER_DDL_TIMEOUT},
            poolclass=NullPool,
        )
        try:
            async with engine.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(go())


@pytest.fixture(scope="session")
def database_url(worker_id: str) -> Iterator[str]:
    """The test database, migrated to the latest revision.

    Under pytest-xdist every worker gets a database of its own, `<TEST_DATABASE_URL's name>_gw0`,
    `..._gw1`, created fresh for the run (so no schema from another branch leaks in) and dropped
    at its end (`GB_KEEP_TEST_DBS=1` keeps it): tests insert the same fixed ids and unique values,
    which on one shared database would wait on each other's row locks. The migrations install the
    extensions. The base database itself is then not used; run `alembic upgrade head` on it for
    `alembic check`.
    """
    base_url = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)
    if not is_local_database(base_url):
        # Never skip this one: tests migrate and roll back, which must not happen on shared Neon.
        pytest.fail("TEST_DATABASE_URL must point at the local Docker database, never at Neon")
    worker = _worker_number(worker_id)
    url = base_url
    name = f"{make_url(base_url).database}_{worker_id}"
    if worker is not None:
        url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
        # The derived URL has the base's host, so the guard above holds for it too.
        assert is_local_database(url)

    async def probe() -> None:
        engine = create_engine(base_url, pooled=False)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(probe())
    except Exception:
        _unavailable("Postgres is not reachable at TEST_DATABASE_URL (run `just up`)")
    if worker is not None:
        run_on_server(base_url, f"DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)")
        run_on_server(base_url, f"CREATE DATABASE {_quote(name)}")
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        if worker is not None and os.environ.get("GB_KEEP_TEST_DBS") != "1":
            run_on_server(base_url, f"DROP DATABASE IF EXISTS {_quote(name)} WITH (FORCE)")


# Valkey database indexes. Tests call FLUSHDB, so concurrent workers must not share a database, nor
# may they land on the base index of another concurrent run (the check script gives each run its
# own base, 0-15). Worker n (gw0 is 0) of base b uses b + 16 * (n + 1): a run's base is index b
# and its workers sit at 16-31 + b, 32-47 + b ..., so at most 7 workers and 128 databases, which
# the compose file sets (`valkey-server --databases 128`; recreate the container once with
# `docker compose -f infra/compose/docker-compose.yml up -d valkey`).
VALKEY_BASE_DATABASES = 16
VALKEY_MAX_WORKERS = 7


def worker_valkey_url(url: str, worker: int | None) -> str:
    """`url` with its database index moved to the one reserved for xdist worker `worker`."""
    if worker is None:
        return url
    parts = urlsplit(url)
    base = int(parts.path.lstrip("/") or 0)
    if not 0 <= base < VALKEY_BASE_DATABASES:
        pytest.fail(f"TEST_VALKEY_URL's database must be 0-15 to run in parallel, not {base}")
    if worker >= VALKEY_MAX_WORKERS:
        pytest.fail(
            f"at most {VALKEY_MAX_WORKERS} xdist workers fit the Valkey databases, not {worker + 1}"
        )
    return urlunsplit(parts._replace(path=f"/{base + VALKEY_BASE_DATABASES * (worker + 1)}"))


@pytest.fixture(scope="session")
def valkey_url(worker_id: str) -> str:
    url = worker_valkey_url(
        os.environ.get("TEST_VALKEY_URL", LOCAL_TEST_VALKEY_URL), _worker_number(worker_id)
    )

    async def probe() -> None:
        client = create_valkey(url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(probe())
    except Exception as error:
        if "out of range" in str(error):
            pytest.fail(
                f"Valkey has too few databases for {url}: recreate it with --databases 128 "
                "(`docker compose -f infra/compose/docker-compose.yml up -d valkey`)"
            )
        _unavailable("Valkey is not reachable at TEST_VALKEY_URL (run `just up`)")
    return url


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Start the one test that takes minutes (the fuzzer, one item) first: under xdist it then
    runs beside the others, not alone at the end. Stable: every worker collects the same order."""
    items.sort(key=lambda item: item.path.name != "test_api_fuzz.py")


@pytest.fixture(autouse=True)
def _settings_in_use() -> Iterator[None]:
    """Module code outside a request reads `get_settings()` (a refund to the payment source made by
    `refund_to_wallet`): without an app, tests give it the test settings; `create_app` replaces
    them with its own."""
    use_settings(make_settings())
    yield
    use_settings(None)


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
