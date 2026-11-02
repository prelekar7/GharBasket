"""The helpers behind scripts/staging-demo.sh (gharbasket_sim.staging), and the script's own
argument handling and dry run on the local test database."""

import asyncio
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from gharbasket_sim import staging
from gharbasket_sim.staging import AlreadySimulated, NothingToAdd, Window, plan_window

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "staging-demo.sh"
TODAY = date(2026, 11, 2)  # a Monday: the last full week starts on 26 October
LAST = date(2026, 10, 26)


def mondays(first: date, n: int) -> list[date]:
    return [first + timedelta(weeks=w) for w in range(n)]


@pytest.mark.parametrize("today", [TODAY, date(2026, 11, 5), date(2026, 11, 8)])
def test_the_window_is_the_last_full_weeks_before_this_ist_week(today: date) -> None:
    assert plan_window(today, 6, [], add_weeks=False) == Window(date(2026, 9, 21), 6)
    assert plan_window(today, 1, [], add_weeks=False) == Window(LAST, 1)


def test_weeks_outside_the_window_do_not_count() -> None:
    # Older simulated weeks, and the current week staging's own job drafted, are left alone.
    simulated = [date(2026, 8, 3), date(2026, 11, 2), date(2026, 11, 9)]
    assert plan_window(TODAY, 6, simulated, add_weeks=False) == Window(date(2026, 9, 21), 6)


def test_a_window_with_simulated_weeks_is_refused_without_add_weeks() -> None:
    with pytest.raises(AlreadySimulated, match=r"2 of the weeks .* \(latest 2026-10-05\)"):
        plan_window(TODAY, 6, mondays(date(2026, 9, 28), 2), add_weeks=False)


def test_add_weeks_simulates_only_the_weeks_after_the_latest() -> None:
    simulated = mondays(date(2026, 9, 21), 4)  # up to 12 October
    assert plan_window(TODAY, 6, simulated, add_weeks=True) == Window(date(2026, 10, 19), 2)
    # Nothing simulated yet: the whole window; long ago: no more than --weeks.
    assert plan_window(TODAY, 6, [], add_weeks=True) == Window(date(2026, 9, 21), 6)
    assert plan_window(TODAY, 2, [date(2026, 6, 1)], add_weeks=True) == Window(
        date(2026, 10, 19), 2
    )
    with pytest.raises(NothingToAdd, match="2026-10-26"):
        plan_window(TODAY, 6, mondays(date(2026, 9, 21), 6), add_weeks=True)
    with pytest.raises(ValueError, match="at least 1"):
        plan_window(TODAY, 0, [], add_weeks=False)


def test_the_estimate_follows_the_adoption() -> None:
    assert staging.expected_households(0.005) == 4
    assert staging.expected_households(0.006) == 5
    assert staging.expected_households(0.1) > 80
    text = staging.estimate(5, 6)
    assert text.startswith("5 households x 6 weeks = 30 household-weeks")
    assert "minutes against Neon" in text


def test_the_window_command_prints_the_plan_and_exits_3_or_4(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    taken: list[date] = []
    monkeypatch.setattr(staging, "database_url", lambda: "postgresql://unused@localhost/x")
    monkeypatch.setattr(staging, "simulated_weeks", lambda url: taken)
    argv = ["window", "--weeks", "6", "--today", TODAY.isoformat()]

    assert staging.main(argv) == 0
    assert capsys.readouterr().out == "2026-09-21 6\n"

    taken.extend(mondays(date(2026, 9, 21), 6))
    assert staging.main(argv) == staging.EXIT_ALREADY_SIMULATED
    assert "--add-weeks" in capsys.readouterr().err
    assert staging.main([*argv, "--add-weeks"]) == staging.EXIT_NOTHING_TO_ADD
    with pytest.raises(SystemExit):
        staging.main(["window", "--weeks", "0"])


def test_the_estimate_command_needs_no_database(capsys: pytest.CaptureFixture[str]) -> None:
    assert staging.main(["estimate", "--weeks", "2", "--adoption", "0.006"]) == 0
    assert capsys.readouterr().out.startswith("5 households x 2 weeks")


@pytest.mark.services
def test_counts_and_weeks_read_the_database(database_url: str) -> None:
    counts = staging.counts(database_url)
    assert set(counts) == set(staging.COUNTS)
    assert all(isinstance(n, int) and n >= 0 for n in counts.values())
    weeks = staging.simulated_weeks(database_url)
    assert all(isinstance(d, date) and d.weekday() == 0 for d in weeks)


@pytest.mark.services
def test_the_host_command_names_the_host_only(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL_DIRECT", database_url)
    assert staging.main(["host"]) == 0
    assert capsys.readouterr().out == "localhost\n"
    assert staging.main(["counts"]) == 0
    assert "weekly cycles (synthetic)" in capsys.readouterr().out
    assert asyncio.run(staging._read(database_url, ["SELECT 1"])) == [[(1,)]]  # pyright: ignore[reportPrivateUsage]


def script(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=300,
        check=False,
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--weeks", "0"], "--weeks must be"),
        (["--weeks", "27"], "--weeks must be"),
        (["--weeks", "six"], "--weeks must be"),
        (["--adoption", "1.5"], "--adoption must be"),
        (["--adoption", "0"], "--adoption must be"),
        (["--planner", "magic"], "--planner must be"),
        (["--seed", "-1"], "--seed must be"),
        (["--weeks"], "--weeks needs a value"),
        (["--force"], "unknown option: --force"),
    ],
)
def test_the_script_refuses_bad_arguments_before_touching_anything(
    args: list[str], message: str
) -> None:
    done = script(*args)
    assert done.returncode == 2
    assert message in done.stderr


def test_the_script_explains_itself() -> None:
    done = script("--help")
    assert done.returncode == 0
    assert "GB_ALLOW_REMOTE_SEED" in done.stdout


@pytest.mark.services
def test_the_script_dry_run_on_the_local_database_writes_nothing(database_url: str) -> None:
    before = staging.counts(database_url)
    done = script(
        "--dry-run",
        "--weeks",
        "2",
        env={"DATABASE_URL": database_url, "DATABASE_URL_DIRECT": database_url},
    )
    assert done.returncode == 0, done.stderr
    assert "database host: localhost (local Docker)" in done.stdout
    assert "plan: 2 week(s) from Monday" in done.stdout
    assert "dry run: nothing written" in done.stdout
    assert staging.counts(database_url) == before


@pytest.mark.services
def test_the_script_refuses_a_checkout_that_is_not_the_deployed_commit(database_url: str) -> None:
    # `localhost.` is the same server, but not a local host name: the shared-database path.
    shared = database_url.replace("@localhost:", "@localhost.:")
    before = staging.counts(database_url)
    done = script(
        "--weeks",
        "1",
        env={
            "DATABASE_URL": shared,
            "DATABASE_URL_DIRECT": shared,
            "GB_ALLOW_REMOTE_SEED": "localhost.",
            "STAGING_API_URL": "http://127.0.0.1:9",  # nothing answers: an unknown commit
            "NEON_API_KEY": "",
        },
    )
    assert done.returncode == 2, done.stdout + done.stderr
    assert "database host: localhost. (shared)" in done.stdout
    assert "unlocking societies the run adds households to:" in done.stdout
    assert "runs an unknown commit" in done.stderr
    assert staging.counts(database_url) == before
