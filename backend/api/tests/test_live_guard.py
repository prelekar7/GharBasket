"""The script guards of live mode (M5 §3.10, "Guards outside the API"; ADR-0037 §4): the seed,
the simulator's shared path, the demo loader and the rollback refuse a database that takes real
money."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools import live_guard
from gharbasket.tools.live_guard import (
    CUTOVER_KIND,
    LiveMoneyRefused,
    entry_of_kind_exists,
    refuse_if_live,
)

NEON_HOST = "ep-x.ap-southeast-1.aws.neon.tech"
NEON = f"postgresql://app:secret-pw@{NEON_HOST}/neondb?sslmode=require"
LOCAL = "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket"


@pytest.fixture
def cutover(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The database lookup replaced by one that finds no cut-over entry: the URLs it was asked."""
    asked: list[str] = []

    def recorded(url: str) -> bool:
        asked.append(url)
        return False

    monkeypatch.setattr(live_guard, "cutover_recorded", recorded)
    return asked


@pytest.mark.parametrize("command", sorted(live_guard.COMMANDS))
def test_the_local_database_is_never_refused_nor_asked(cutover: list[str], command: str) -> None:
    refuse_if_live(command, LOCAL, {"RAZORPAY_MODE": "live"})

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
    monkeypatch.setattr(live_guard, "cutover_recorded", lambda url: True)

    with pytest.raises(LiveMoneyRefused) as refused:
        refuse_if_live("staging-rollback", NEON, environ)

    message = str(refused.value)
    assert f"holds the {CUTOVER_KIND} journal entry" in message
    assert "erasing rows of money" in message
    assert "secret-pw" not in message


def test_a_database_that_cannot_be_read_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(url: str) -> bool:
        raise OperationalError("SELECT 1", {}, OSError(f"cannot reach {url}"))

    monkeypatch.setattr(live_guard, "cutover_recorded", unreachable)

    with pytest.raises(LiveMoneyRefused) as refused:
        refuse_if_live("seed", NEON, {})

    message = str(refused.value)
    assert "could not check" in message and "OperationalError" in message
    assert "secret-pw" not in message


def test_test_mode_with_no_cutover_entry_passes(cutover: list[str]) -> None:
    refuse_if_live("sim", NEON, {"RAZORPAY_MODE": "test"})

    assert cutover == [NEON]


def test_the_cli_exits_2_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cutover: list[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", NEON)
    monkeypatch.setenv("DATABASE_URL_DIRECT", NEON)
    monkeypatch.setenv("RAZORPAY_MODE", "live")

    assert live_guard.main(["staging-demo"]) == 2

    captured = capsys.readouterr()
    assert "refused: RAZORPAY_MODE=live" in captured.err
    assert "secret-pw" not in captured.err + captured.out

    monkeypatch.setenv("RAZORPAY_MODE", "test")
    assert live_guard.main(["staging-demo"]) == 0


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
