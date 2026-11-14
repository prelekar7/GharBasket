"""Warn before staging's Neon project runs out of free compute hours (handbook 12 §12.5).

    infisical run --env=staging -- uv run python scripts/neon_usage_check.py

Neon's free plan gives a project a fixed number of compute-unit hours a month (NEON_CU_HOURS_LIMIT,
default 100, infra/jobs/schedule.yaml); when they run out the compute is suspended until the next
month, so staging is down. Only storage is watched inside the API (`platform.database_size`), so
this script reads the project's usage from Neon's console API: `compute_time_seconds` (CU-seconds
since `consumption_period_start`, every branch of the project together). It asks Neon's control
plane, never the database, so it does not wake the compute or spend the hours it counts.

It exits 1 with one line per problem when either holds:
- the hours used are at least NEON_CU_HOURS_WARN_PCT (default 80) per cent of the limit;
- at the pace so far the month would end above the limit. The pace is judged only once
  MIN_DAYS_FOR_PACE days of the period have passed, so one busy first morning does not alarm.

`.github/workflows/neon-usage.yml` runs it daily and turns a failure into the healthchecks `ops`
alert (ADR-0033). Needs NEON_API_KEY and DATABASE_URL_DIRECT (or DATABASE_URL), as
neon_restore_point.py does; NEON_PROJECT_ID is optional. It never prints the key or the URL.
"""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from neon_restore_point import (
    Call,
    NeonError,
    endpoint_of,
    find_endpoint,
    http_call,
)

DEFAULT_LIMIT_H = 100.0
DEFAULT_WARN_PCT = 80.0
MIN_DAYS_FOR_PACE = 3.0


@dataclass(frozen=True)
class Usage:
    project_id: str
    cu_hours: float
    period_start: datetime
    period_end: datetime  # the next monthly reset


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC)


def _next_month(start: datetime) -> datetime:
    """The same day and time one calendar month later (clamped to the month's last day)."""
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    day = start.day
    while True:
        try:
            return start.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1


def usage(call: Call, database_url: str, project_id: str | None) -> Usage:
    project, _branch = find_endpoint(call, endpoint_of(database_url), project_id)
    status, answer = call("GET", f"/projects/{project}", None)
    if not 200 <= status < 300:
        raise NeonError(f"project {project}: Neon answered {status} {answer.get('message', '')}")
    body = answer.get("project", {})
    if "compute_time_seconds" not in body or "consumption_period_start" not in body:
        raise NeonError(f"project {project}: Neon sent no compute_time_seconds")
    start = _parse(body["consumption_period_start"])
    return Usage(project, body["compute_time_seconds"] / 3600, start, _next_month(start))


def problems(u: Usage, now: datetime, limit_h: float, warn_pct: float) -> list[str]:
    out: list[str] = []
    used_pct = 100 * u.cu_hours / limit_h
    if used_pct >= warn_pct:
        out.append(
            f"Neon compute: {u.cu_hours:.1f} of {limit_h:g} CU-hours used ({used_pct:.0f} %) "
            f"since {u.period_start:%d %b}; the compute is suspended at {limit_h:g} until "
            f"{u.period_end:%d %b}"
        )
    elapsed = (now - u.period_start) / timedelta(days=1)
    total = (u.period_end - u.period_start) / timedelta(days=1)
    if elapsed >= MIN_DAYS_FOR_PACE and not out:
        projected = u.cu_hours * total / elapsed
        if projected > limit_h:
            out.append(
                f"Neon compute: {u.cu_hours:.1f} CU-hours in {elapsed:.1f} days is on pace for "
                f"{projected:.0f} of {limit_h:g} by {u.period_end:%d %b}; a minute-level job may "
                "be running outside its window (infra/jobs/schedule.yaml)"
            )
    return out


def _number(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise NeonError(f"{name} must be above 0")
    return value


def main(environ: Mapping[str, str] = os.environ, now: datetime | None = None) -> int:
    key = environ.get("NEON_API_KEY")
    url = environ.get("DATABASE_URL_DIRECT") or environ.get("DATABASE_URL")
    if not key or not url:
        print("needs NEON_API_KEY and DATABASE_URL_DIRECT (or DATABASE_URL)", file=sys.stderr)
        return 2
    try:
        limit_h = _number(environ, "NEON_CU_HOURS_LIMIT", DEFAULT_LIMIT_H)
        warn_pct = _number(environ, "NEON_CU_HOURS_WARN_PCT", DEFAULT_WARN_PCT)
        u = usage(http_call(key), url, environ.get("NEON_PROJECT_ID") or None)
    except (NeonError, ValueError, OSError) as err:  # OSError: URLError, timeouts
        print(f"neon usage check failed: {err}", file=sys.stderr)
        return 1
    found = problems(u, now or datetime.now(UTC), limit_h, warn_pct)
    print(
        f"Neon project {u.project_id}: {u.cu_hours:.1f} of {limit_h:g} CU-hours since "
        f"{u.period_start:%Y-%m-%d}, resets {u.period_end:%Y-%m-%d}"
    )
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
