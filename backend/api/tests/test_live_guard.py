"""The script guards (M5 §3.10, "Guards outside the API"; ADR-0036, ADR-0037 §4): the seed, the
simulator's shared path, the demo loader and the rollback refuse production and a database that
takes real money."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.environment import read_environment
from gharbasket.tools import live_guard
from gharbasket.tools.live_guard import (
    CUTOVER_KIND,
    DatabaseFacts,
    LiveMoneyRefused,
    ProductionRefused,
    entry_of_kind_exists,
    refuse_if_live,
)

NEON_HOST = "ep-x.ap-southeast-1.aws.neon.tech"
NEON = f"postgresql://app:secret-pw@{NEON_HOST}/neondb?sslmode=require"
LOCAL = "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket"


STAGING = DatabaseFacts(environment="staging", cutover=False)


@pytest.fixture
def cutover(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The database lookup replaced by one that finds staging's mark and no cut-over entry: the
    URLs it was asked."""
    asked: list[str] = []

    def inspect(url: str) -> DatabaseFacts:
        asked.append(url)
        return STAGING

    monkeypatch.setattr(live_guard, "inspect_database", inspect)
    return asked


@pytest.mark.parametrize("command", sorted(live_guard.COMMANDS))
def test_the_local_database_is_never_refused_nor_asked(cutover: list[str], command: str) -> None:
    refuse_if_live(command, LOCAL, {"RAZORPAY_MODE": "live", "APP_ENV": "prod"})

    assert cutover == []


@pytest.mark.parametrize("mode", ["live", " LIVE ", "Live"])
def test_live_mode_in_the_environment_refuses_a_shared_database(
    cutover: list[str], mode: str
) -> None:
    with pytest.raises(LiveMoneyRefused) as refused:
        refuse_if_live("staging-demo", NEON, {"RAZORPAY_MODE": mode})

    message = str(refused.value)
    assert "RAZORPAY_MODE=live" in message
    assert NEON_HOST in message
    assert "ADR-0037" in message
    assert "secret-pw" not in message and "neondb" not in message
    assert cutover == []  # the variable alone decides; nothing connects


@pytest.mark.parametrize("environ", [{}, {"RAZORPAY_MODE": "test"}])
def test_the_cutover_entry_refuses_even_without_the_variable(
    monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]
) -> None:
    monkeypatch.setattr(
        live_guard, "inspect_database", lambda url: DatabaseFacts("staging", cutover=True)
    )

    with pytest.raises(LiveMoneyRefused) as refused:
        refuse_if_live("staging-rollback", NEON, environ)

    message = str(refused.value)
    assert f"holds the {CUTOVER_KIND} journal entry" in message
    assert "erasing rows of money" in message
    assert "secret-pw" not in message


def test_a_database_that_cannot_be_read_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(url: str) -> DatabaseFacts:
        raise OperationalError("SELECT 1", {}, OSError(f"cannot reach {url}"))

    monkeypatch.setattr(live_guard, "inspect_database", unreachable)

    with pytest.raises(LiveMoneyRefused) as refused:
        refuse_if_live("seed", NEON, {})

    message = str(refused.value)
    assert "could not check" in message and "OperationalError" in message
    assert "secret-pw" not in message


def test_staging_in_test_mode_with_no_cutover_entry_passes(cutover: list[str]) -> None:
    refuse_if_live("sim", NEON, {"RAZORPAY_MODE": "test", "APP_ENV": "staging"})

    assert cutover == [NEON]


@pytest.mark.parametrize("app_env", ["prod", " PROD ", "Prod"])
@pytest.mark.parametrize("command", sorted(live_guard.COMMANDS))
def test_app_env_prod_refuses_a_shared_database_without_asking_it(
    cutover: list[str], command: str, app_env: str
) -> None:
    with pytest.raises(ProductionRefused) as refused:
        refuse_if_live(command, NEON, {"APP_ENV": app_env, "RAZORPAY_MODE": "live"})

    message = str(refused.value)
    assert "APP_ENV=prod" in message and "is production" in message
    assert NEON_HOST in message
    assert "ADR-0036" in message
    assert "secret-pw" not in message and "neondb" not in message
    assert cutover == []


@pytest.mark.parametrize("environ", [{}, {"APP_ENV": "staging", "RAZORPAY_MODE": "test"}])
@pytest.mark.parametrize("command", sorted(live_guard.COMMANDS))
def test_a_database_marked_prod_is_refused_whatever_the_environment_says(
    monkeypatch: pytest.MonkeyPatch, command: str, environ: dict[str, str]
) -> None:
    """A production URL pasted into a staging shell: the database itself says what it is."""
    monkeypatch.setattr(
        live_guard, "inspect_database", lambda url: DatabaseFacts("prod", cutover=False)
    )

    with pytest.raises(ProductionRefused) as refused:
        refuse_if_live(command, NEON, environ)

    message = str(refused.value)
    assert f"{NEON_HOST} is marked prod in platform.environment" in message
    assert live_guard.COMMANDS[command] in message
    assert "secret-pw" not in message and "neondb" not in message


def test_a_database_with_no_mark_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: a shared database that no deploy has marked cannot be shown not to be
    production."""
    monkeypatch.setattr(live_guard, "inspect_database", lambda url: DatabaseFacts(None, False))

    with pytest.raises(ProductionRefused) as refused:
        refuse_if_live("staging-demo", NEON, {"APP_ENV": "staging"})

    message = str(refused.value)
    assert "has no platform.environment mark" in message and "redeploy staging" in message
    assert "secret-pw" not in message


@pytest.mark.parametrize("environment", ["dev", "test", "staging"])
def test_any_other_mark_passes(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setattr(
        live_guard, "inspect_database", lambda url: DatabaseFacts(environment, cutover=False)
    )

    refuse_if_live("seed", NEON, {})


def test_the_cli_exits_2_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cutover: list[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", NEON)
    monkeypatch.setenv("DATABASE_URL_DIRECT", NEON)
    monkeypatch.setenv("RAZORPAY_MODE", "live")
    monkeypatch.delenv("APP_ENV", raising=False)

    assert live_guard.main(["staging-demo"]) == 2

    captured = capsys.readouterr()
    assert "refused: RAZORPAY_MODE=live" in captured.err
    assert "secret-pw" not in captured.err + captured.out

    monkeypatch.setenv("RAZORPAY_MODE", "test")
    assert live_guard.main(["staging-demo"]) == 0

    monkeypatch.setenv("APP_ENV", "prod")
    assert live_guard.main(["staging-demo"]) == 2
    assert "refused: APP_ENV=prod" in capsys.readouterr().err


@pytest.mark.anyio
@pytest.mark.services
async def test_the_lookup_finds_the_cutover_entry(db_session: AsyncSession) -> None:
    connection = await db_session.connection()
    assert not await entry_of_kind_exists(connection, CUTOVER_KIND)

    # The kind arrives with the cut-over PR (M5 §3.10); until then the check constraint is
    # lifted inside this test's transaction, which is rolled back.
    await connection.execute(
        text(
            "ALTER TABLE billing.journal_entries DROP CONSTRAINT IF EXISTS ck_journal_entries_kind"
        )
    )
    await connection.execute(
        text(
            "INSERT INTO billing.journal_entries (kind, ref_type, idempotency_key) "
            "VALUES (:kind, NULL, :kind)"
        ),
        {"kind": CUTOVER_KIND},
    )

    assert await entry_of_kind_exists(connection, CUTOVER_KIND)


@pytest.mark.anyio
@pytest.mark.services
async def test_the_lookup_is_false_before_the_billing_tables_exist(
    db_session: AsyncSession,
) -> None:
    connection = await db_session.connection()
    await connection.execute(text("ALTER TABLE billing.journal_entries RENAME TO gone"))

    assert not await entry_of_kind_exists(connection, CUTOVER_KIND)


@pytest.mark.anyio
@pytest.mark.services
async def test_the_lookup_reads_the_environment_mark(db_session: AsyncSession) -> None:
    connection = await db_session.connection()
    assert await read_environment(connection) is None  # the local database is never marked

    await connection.execute(text("INSERT INTO platform.environment (name) VALUES ('prod')"))
    assert await read_environment(connection) == "prod"

    await connection.execute(text("ALTER TABLE platform.environment RENAME TO gone"))
    assert await read_environment(connection) is None  # before the table exists
