import asyncio

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text

from gharbasket.main import create_app
from gharbasket.platform.db import SCHEMAS, asyncpg_url, create_engine

from .conftest import add_test_routes, alembic_config, make_settings

NEON_POOLED = (
    "postgresql://app:pw@ep-cool-name-pooler.ap-southeast-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def test_neon_url_is_made_safe_for_asyncpg_behind_pgbouncer() -> None:
    url, connect_args = asyncpg_url(NEON_POOLED)

    assert url.drivername == "postgresql+asyncpg"
    assert dict(url.query) == {"prepared_statement_cache_size": "0"}
    assert connect_args["ssl"] == "require"
    assert connect_args["statement_cache_size"] == 0
    name = connect_args["prepared_statement_name_func"]
    assert name() != name()


def test_local_url_does_not_ask_for_ssl() -> None:
    _, connect_args = asyncpg_url("postgresql+asyncpg://gb:gb@localhost:5432/gharbasket")

    assert "ssl" not in connect_args


def test_migrations_prefer_the_direct_url() -> None:
    settings = make_settings(
        database_url="postgresql://pooled", database_url_direct="postgresql://direct"
    )

    assert settings.migrations_url == "postgresql://direct"
    assert make_settings(database_url="postgresql://pooled").migrations_url == "postgresql://pooled"


def test_migrations_have_a_single_head() -> None:
    script = ScriptDirectory.from_config(alembic_config("postgresql://unused"))

    assert len(script.get_heads()) == 1


def test_ready_fails_without_a_database() -> None:
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "platform.not_ready"
    assert response.json()["errors"] == [{"check": "database", "result": "not_configured"}]


def test_ready_fails_without_leaking_connection_details() -> None:
    settings = make_settings(database_url="postgresql://gb:secret-pw@127.0.0.1:1/gharbasket")
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 503
    assert response.json()["errors"] == [{"check": "database", "result": "failed"}]
    assert "127.0.0.1" not in response.text
    assert "secret-pw" not in response.text


def test_session_needs_a_configured_database() -> None:
    app = create_app(make_settings())
    add_test_routes(app)
    with TestClient(app) as client:
        response = client.get("/v1/_test/db")

    assert response.status_code == 503
    assert response.json()["code"] == "platform.database_unavailable"


@pytest.mark.services
def test_ready_with_database_and_valkey(database_url: str, valkey_url: str) -> None:
    settings = make_settings(database_url=database_url, valkey_url=valkey_url)
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "valkey": "ok"}}


@pytest.mark.services
def test_ready_skips_valkey_when_not_configured(database_url: str) -> None:
    with TestClient(create_app(make_settings(database_url=database_url))) as client:
        response = client.get("/v1/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "valkey": "skipped"}


@pytest.mark.services
def test_request_gets_a_working_session(database_url: str) -> None:
    app = create_app(make_settings(database_url=database_url))
    add_test_routes(app)
    with TestClient(app) as client:
        assert client.get("/v1/_test/db").json() == 42


def _schemas(database_url: str) -> set[str]:
    async def query() -> set[str]:
        engine = create_engine(database_url, pooled=False)
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(text("SELECT nspname FROM pg_namespace"))
                return {row[0] for row in rows}
        finally:
            await engine.dispose()

    return asyncio.run(query())


@pytest.mark.services
def test_migrations_upgrade_downgrade_round_trip(database_url: str) -> None:
    config = alembic_config(database_url)

    command.downgrade(config, "base")
    assert _schemas(database_url) & set(SCHEMAS) == {"platform"}

    command.upgrade(config, "head")
    assert set(SCHEMAS) <= _schemas(database_url)
