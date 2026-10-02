"""Safety rails: settings defaults, secrets, the operator token, log scrubbing and the guards that
keep tests and unmerged migrations away from the shared Neon database."""

import json
import logging

import httpx2
import pytest
import structlog
from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.db import SCHEMAS, is_local_database
from gharbasket.platform.logging import configure_logging, scrub
from gharbasket.platform.settings import Settings

from .conftest import DEPLOYED_SECRETS, alembic_config, make_settings
from .test_db import _schemas


def test_without_app_env_the_app_runs_as_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    for name, value in DEPLOYED_SECRETS.items():
        monkeypatch.setenv(name.upper(), str(value))

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.app_env == "prod"
    assert not settings.docs_enabled
    assert not settings.is_local


def test_deployed_environments_refuse_to_start_without_a_jobs_token() -> None:
    with pytest.raises(ValidationError, match="JOBS_TOKEN"):
        make_settings(**{**DEPLOYED_SECRETS, "jobs_token": None}, app_env="staging")


def test_secrets_never_appear_in_the_settings_repr() -> None:
    settings = make_settings(
        database_url="postgresql://app:s3cr3t@db.example/gb", jobs_token="op-token-123"
    )

    assert "s3cr3t" not in repr(settings)
    assert "op-token-123" not in repr(settings)
    assert settings.migrations_url == "postgresql://app:s3cr3t@db.example/gb"


def test_log_level_is_validated_and_case_insensitive() -> None:
    assert make_settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        make_settings(log_level="LOUD")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("call +91 98765 43210 now", "call [phone ••10] now"),
        ("9876543210", "[phone ••10]"),
        ("Authorization: Bearer abc.def-123", "Authorization: Bearer [redacted]"),
        ("token eyJhbGciOi.eyJzdWIi.c2ln", "token [jwt]"),
        (
            "postgresql://app:s3cr3t@ep-x.neon.tech/db",
            "postgresql://app:[redacted]@ep-x.neon.tech/db",
        ),
        # Not personal data: must stay readable.
        ("order 1234567890 total 98765432100 paise", "order 1234567890 total 98765432100 paise"),
        ("request a1b29876543210ff", "request a1b29876543210ff"),
    ],
)
def test_scrub(raw: str, expected: str) -> None:
    assert scrub(raw) == expected


def test_log_lines_and_tracebacks_are_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    log = structlog.get_logger("gharbasket.test")
    try:
        raise RuntimeError("OTP failed for +919876543210")
    except RuntimeError:
        log.exception("login_failed", phone="9876543210", headers={"authorization": "Bearer abc"})
    logging.getLogger("uvicorn.error").warning("bad url postgresql://u:pw@host/db")

    output = capsys.readouterr().out
    lines = [json.loads(line) for line in output.strip().splitlines()]
    assert lines[0]["phone"] == "[phone ••10]"
    assert lines[0]["headers"] == {"authorization": "Bearer [redacted]"}
    assert "9876543210" not in output
    assert "pw@" not in output
    assert "[phone ••10]" in lines[0]["exception"]


def test_ready_needs_the_operator_token_when_deployed() -> None:
    settings = make_settings(app_env="staging", **DEPLOYED_SECRETS)
    with TestClient(create_app(settings)) as client:
        missing = client.get("/v1/ready")
        wrong = client.get("/v1/ready", headers={"Authorization": "Bearer nope"})
        right = client.get("/v1/ready", headers={"Authorization": "Bearer op-token"})

    assert missing.status_code == 401
    assert missing.json()["code"] == "platform.operator_token_required"
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    # Authorised; no database is configured here, so it is "not ready" rather than refused.
    assert right.status_code == 503


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("postgresql+asyncpg://gb:gb@localhost:5432/gharbasket_test", True),
        ("postgresql://gb:gb@127.0.0.1/gharbasket", True),
        ("postgresql://app:pw@ep-cool-name-pooler.ap-southeast-1.aws.neon.tech/neondb", False),
    ],
)
def test_local_database_detection(url: str, local: bool) -> None:
    assert is_local_database(url) is local


def test_tests_refuse_a_remote_database() -> None:
    # The fixture itself is exercised implicitly; this documents the rule it relies on.
    assert not is_local_database("postgresql://app:pw@ep-x.neon.tech/neondb")


def test_migrations_refuse_a_shared_database_without_the_deploy_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GB_ALLOW_REMOTE_MIGRATIONS", raising=False)
    remote = "postgresql://app:pw@ep-cool-name.ap-southeast-1.aws.neon.tech/neondb"

    with pytest.raises(SystemExit, match="Refusing to migrate a shared"):
        command.upgrade(alembic_config(remote), "head")


@pytest.mark.services
def test_downgrades_are_refused_on_a_shared_database(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.0.0.0 reaches the same Docker database but is not on the local list, so the database is
    # treated as shared: upgrades need the deploy flag, and downgrades are refused outright.
    shared = database_url.replace("localhost", "0.0.0.0")
    monkeypatch.setenv("GB_ALLOW_REMOTE_MIGRATIONS", "1")

    with pytest.raises(SystemExit, match="Refusing to downgrade"):
        command.downgrade(alembic_config(shared), "base")

    # The refused downgrade was rolled back: every schema is still there.
    assert set(SCHEMAS) <= _schemas(database_url)


@pytest.mark.anyio
@pytest.mark.services
async def test_requests_share_the_tests_rolled_back_session(
    db_session: AsyncSession, api: httpx2.AsyncClient
) -> None:
    await db_session.execute(text("CREATE TABLE platform.rollback_probe (id int)"))
    await db_session.commit()  # only releases a savepoint inside the test's transaction

    response = await api.get("/v1/_test/db")
    probe = await db_session.execute(text("SELECT to_regclass('platform.rollback_probe')"))

    assert response.status_code == 200
    assert response.json() == 42
    assert probe.scalar_one() is not None


@pytest.mark.anyio
@pytest.mark.services
async def test_nothing_survives_the_previous_test(db_session: AsyncSession) -> None:
    probe = await db_session.execute(text("SELECT to_regclass('platform.rollback_probe')"))

    assert probe.scalar_one() is None
