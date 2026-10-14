"""Every process loads every module's jobs and outbox consumers (modules/registry.py), and the
outbox relay refuses to run in one that has not (platform/outbox.py)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules import MODULES
from gharbasket.modules.notifications import routes
from gharbasket.modules.registry import jobs_modules, load_modules
from gharbasket.platform import outbox
from gharbasket.platform.jobs import JOBS
from gharbasket.platform.models import OutboxEvent

SRC = Path(__file__).resolve().parents[1] / "src" / "gharbasket"
MODULES_DIR = SRC / "modules"
JOB_NAME = re.compile(r'^@job\(\s*"([a-z_]+)"', re.MULTILINE)
SUBSCRIBE = re.compile(r"^\s*subscribe\(", re.MULTILINE)


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _job_names_in_source() -> set[str]:
    return {name for path in _sources() for name in JOB_NAME.findall(path.read_text())}


def _fresh_python(code: str) -> str:
    """Run `code` in a new interpreter: nothing the test session imported is registered there."""
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env={**os.environ, "PYTHONWARNINGS": "ignore"},
    )
    return done.stdout


def test_every_jobs_py_is_loaded() -> None:
    on_disk = sorted(path.parent.name for path in MODULES_DIR.glob("*/jobs.py"))
    assert set(on_disk) <= set(MODULES), "a module folder is missing from modules.MODULES"
    assert sorted(name.split(".")[2] for name in jobs_modules()) == on_disk


def test_jobs_and_consumers_register_only_in_a_jobs_py() -> None:
    """The loader imports `jobs.py`; a `@job` or `subscribe(...)` anywhere else could be missed."""
    stray = [
        str(path.relative_to(SRC))
        for path in _sources()
        if path.name != "jobs.py"
        and path != SRC / "platform" / "outbox.py"
        and (JOB_NAME.search(path.read_text()) or SUBSCRIBE.search(path.read_text()))
    ]
    assert stray == []


def test_loading_is_idempotent_and_registers_everything() -> None:
    before_jobs = dict(JOBS)
    before = {event: list(handlers) for event, handlers in outbox.CONSUMERS.items()}
    load_modules()
    load_modules()
    assert before_jobs == JOBS
    assert {e: list(h) for e, h in outbox.CONSUMERS.items() if h} == {
        e: h for e, h in before.items() if h
    }
    assert set(JOBS) == _job_names_in_source()
    assert set(routes.ROUTES) <= set(outbox.CONSUMERS)
    assert outbox.consumers_loaded()


def test_a_fresh_process_loads_what_the_app_registers() -> None:
    """The loader alone (as a tool runs it) gives the same jobs and consumers as the app."""
    # The test session built the app (conftest imports gharbasket.main and its routers).
    app_consumers = sorted(event for event, handlers in outbox.CONSUMERS.items() if handlers)
    out = _fresh_python(
        "from gharbasket.platform import jobs, outbox\n"
        "assert not outbox.consumers_loaded()\n"
        "from gharbasket.modules.registry import load_modules\n"
        "load_modules()\n"
        "print(','.join(sorted(jobs.JOBS)))\n"
        "print(','.join(sorted(e for e, h in outbox.CONSUMERS.items() if h)))\n"
        "print(outbox.consumers_loaded())\n"
    )
    jobs_line, consumers_line, loaded = out.strip().splitlines()
    assert jobs_line.split(",") == sorted(JOBS)
    assert consumers_line.split(",") == app_consumers
    assert loaded == "True"


def test_just_job_knows_every_job() -> None:
    """`just job <name>` (tools/run_job.py), in its own process, accepts every `@job` there is."""
    out = _fresh_python(
        "from gharbasket.tools import run_job\n"
        "try:\n"
        "    run_job.main(['--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
    )
    listed = " ".join(out.split())
    for name in _job_names_in_source():
        assert re.search(rf"\b{name}\b", listed), f"`just job` does not know {name!r}"


@pytest.mark.anyio
@pytest.mark.services
async def test_the_relay_refuses_until_the_consumers_are_loaded(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = await outbox.publish(db_session, "identity.nobody_listens", {"id": "x"})
    monkeypatch.setattr(outbox, "_registry", outbox._Registry())  # pyright: ignore[reportPrivateUsage]
    assert not outbox.consumers_loaded()

    with pytest.raises(outbox.ConsumersNotLoadedError):
        await outbox.relay(db_session)
    event = (
        await db_session.execute(select(OutboxEvent).where(OutboxEvent.id == event_id))
    ).scalar_one()
    assert (event.sent_at, event.attempts) == (None, 0)

    outbox.mark_consumers_loaded()
    relayed = await outbox.relay(db_session)
    await db_session.refresh(event)
    assert relayed.sent >= 1 and event.sent_at is not None
