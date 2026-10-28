"""Fixtures of the simulated day: one fresh database for the whole run (created here, dropped at
the end unless `E2E_KEEP_DB=1`), one event loop, and the app started once."""

import asyncio
from collections.abc import Iterator

import pytest
from harness import IST, SUMMARY, App, Config, flush_valkey, recreate_database

_APP: list[App] = []  # the started app, for the summary at the end


@pytest.fixture(scope="session")
def config() -> Config:
    found = Config.from_env()
    found.check()
    return found


@pytest.fixture(scope="session")
def loop() -> Iterator[asyncio.Runner]:
    """One event loop for the run, so the app, its pool and its background relays live across
    the day's steps (each step is `loop.run(...)` inside the clock it happens at)."""
    with asyncio.Runner() as runner:
        yield runner


async def _recreate(config: Config, *, drop_only: bool = False) -> None:
    await recreate_database(config.database_url, create=not drop_only)
    await flush_valkey(config.valkey_url)


@pytest.fixture(scope="session")
def fresh_db(config: Config, loop: asyncio.Runner) -> Iterator[str]:
    """An empty database of the suite's own (and its Valkey db flushed)."""
    loop.run(_recreate(config))
    yield config.database_url
    if not config.keep_db:
        loop.run(_recreate(config, drop_only=True))


@pytest.fixture(scope="session")
def app(config: Config, loop: asyncio.Runner, fresh_db: str) -> Iterator[App]:
    started = App(config)
    loop.run(started.start())
    _APP.append(started)
    yield started
    loop.run(started.stop())


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """The simulated day first: it migrates, seeds and simulates the weeks the journeys build on."""
    items.sort(key=lambda item: item.path.name != "test_simulated_day.py")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """The day's job runs, in order, so a run's log says what the day did."""
    if not _APP:
        return
    write = terminalreporter.write_line
    if SUMMARY:
        terminalreporter.section("the simulated weeks before the day")
        for line in SUMMARY:
            write(line)
    terminalreporter.section("the simulated day: job runs (IST)")
    for run in _APP[0].jobs:
        if run.name == "outbox_sweep" and run.summary and not run.summary.get("sent"):
            continue  # the empty sweeps after each step
        when = run.at.astimezone(IST).strftime("%a %d %b %H:%M")
        write(f"{when}  {run.name:<26} {run.status:<6} {run.summary}")
    write(f"pushes sent to the fake FCM: {len(_APP[0].outside.pushes)}")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """The simulated day first: it migrates, seeds and simulates the weeks the journeys build on."""
    items.sort(key=lambda item: item.path.name != "test_simulated_day.py")
