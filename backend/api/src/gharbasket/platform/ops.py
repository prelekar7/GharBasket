"""Ops probes: what the `ops_check` watchdog looks at (M0 §6.1, ADR-0033).

A probe is a plain async function registered with `@probe("<module>.<name>")` in the module's
`jobs.py`, like jobs and consumers. It reads only its own schema and returns one line per problem
it finds, or nothing. `ops_check` (platform/jobs.py) runs them all and fails, naming each problem,
so the VM scheduler sends them to the `ops` alert.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger("gharbasket.ops")

ProbeFn = Callable[[AsyncSession, datetime], Awaitable[list[str]]]
PROBES: dict[str, ProbeFn] = {}
PROBE_NAME = re.compile(r"^[a-z_]+\.[a-z_]+$")


def probe(name: str) -> Callable[[ProbeFn], ProbeFn]:
    """Register `fn` as the probe `name` (`<module>.<name>`, snake_case, unique)."""

    def register(fn: ProbeFn) -> ProbeFn:
        if not PROBE_NAME.match(name):
            raise ValueError(f"probe names are <module>.<name> in snake_case, got {name!r}")
        if name in PROBES and PROBES[name] is not fn:
            raise ValueError(f"probe {name!r} is already registered")
        PROBES[name] = fn
        return fn

    return register


async def run_probes(session: AsyncSession, now: datetime) -> list[str]:
    """Every probe's problems as `<probe>: <problem>` lines, in name order. A probe that raises is
    a problem itself; it runs in a savepoint, so the others still run."""
    problems: list[str] = []
    for name, fn in sorted(PROBES.items()):
        try:
            async with session.begin_nested():
                found = await fn(session, now)
        except Exception as exc:
            log.error("probe_failed", probe=name, error=type(exc).__name__)
            found = [f"probe failed {type(exc).__name__}"]
        problems += [f"{name}: {line}" for line in found]
    return problems
