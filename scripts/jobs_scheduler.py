"""Call staging's scheduled jobs from the VM (ADR-0032): `vm` entries of infra/jobs/schedule.yaml.

A small daemon for the Oracle VM in Mumbai (`scripts/jobs-on-vm.sh setup` installs it as the
systemd service `gharbasket-jobs`). At the start of every minute it reads the schedule, and for
each `vm` entry with a cron (IST) that names the minute it POSTs `{"scheduled_for": <the minute>}`
to `<API_URL>/v1/internal/jobs/<name>` with JOBS_TOKEN. Each call runs in its own thread, so a slow
job never holds back the next minute.

A call that did not reach the app (HTTP 429, 502, 503 or 504, a connection error, a timeout) is
tried again after 15 s, 45 s and 2 min with the same `scheduled_for`; the job's advisory lock and
its use of `scheduled_for` make a repeat harmless. The app's own answers (2xx, 401, 404, a 500
`platform.job_failed`) are final: the job's next minute is the retry, as with any cron.

The last minute it dispatched is kept in a state file, so a restart neither repeats a minute nor
drops the current one. Every 15 minutes it pulls the checkout's branch; when this file changed, it
exits once no call is in flight and systemd starts the new code. A schedule that no longer parses
is logged and the last good one is kept. HC_PING_URL, if set, is pinged every hour while it runs
and with `/fail` when a call gives up.

    API_URL=... JOBS_TOKEN=... python3 scripts/jobs_scheduler.py      # run (the service does this)
    python3 scripts/jobs_scheduler.py --due 2026-11-05T21:00             # the jobs due then (IST)

Standard library and PyYAML only: the VM runs it with Ubuntu's python3 and python3-yaml.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / "infra" / "jobs" / "schedule.yaml"
STATE = Path.home() / ".local" / "state" / "gharbasket" / "jobs-last-minute"
IST = ZoneInfo("Asia/Kolkata")
RUNNER = "vm"
# (low, high) of the five cron fields; day-of-week 7 is Sunday too.
FIELDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
RETRY_CODES = {429, 502, 503, 504}  # the host's edge answering, not the app
RETRY_AFTER_S = (15, 45, 120)
CALL_TIMEOUT_S = 300  # a cold start (~1 min) plus the longest job
PULL_EVERY_MIN = 15
LATE_START_S = 50  # a (re)start this far into a minute leaves the minute alone


def log(*parts: object) -> None:
    print(*parts, flush=True)


def field_values(field: str, low: int, high: int) -> set[int]:
    """The values a cron field names: `*`, `a`, `a-b`, `*/n`, `a/n`, `a-b/n` and comma lists."""
    values: set[int] = set()
    for part in field.split(","):
        span, _, step = part.partition("/")
        if span == "*":
            start, end = low, high
        elif "-" in span:
            first, _, last = span.partition("-")
            start, end = int(first), int(last)
        else:
            start = end = int(span)
            if step:
                end = high
        stride = int(step) if step else 1
        if not (low <= start <= end <= high and stride >= 1):
            raise ValueError(f"{part!r} is outside {low}-{high}")
        values |= set(range(start, end + 1, stride))
    return values


def parse(cron: str) -> list[set[int]]:
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"{cron!r} has {len(fields)} fields, not 5")
    return [field_values(f, lo, hi) for f, (lo, hi) in zip(fields, FIELDS, strict=True)]


def matches(cron: str, at: datetime) -> bool:
    """Whether a cron names the minute `at` (in its own timezone), with cron's day rule: when both
    day fields are restricted, either one matching is enough."""
    minutes, hours, mdays, months, wdays = parse(cron)
    if at.minute not in minutes or at.hour not in hours or at.month not in months:
        return False
    wday = at.isoweekday() % 7  # cron: 0 (or 7) is Sunday
    on_mday, on_wday = at.day in mdays, wday in wdays or (wday == 0 and 7 in wdays)
    fields = cron.split()
    if fields[2] != "*" and fields[4] != "*":
        return on_mday or on_wday
    return on_mday and on_wday


def load(path: Path = SCHEDULE) -> list[tuple[str, list[str]]]:
    """(name, crons) of every `vm` entry; raises when the file or a cron does not parse."""
    out = []
    for entry in yaml.safe_load(path.read_text())["jobs"]:
        if entry["runner"] != RUNNER:
            continue
        crons = [entry["cron"]] if isinstance(entry["cron"], str) else list(entry["cron"])
        for cron in crons:
            parse(cron)
        out.append((entry["name"], crons))
    return out


def due(schedule: list[tuple[str, list[str]]], minute: datetime) -> list[str]:
    """The jobs whose crons name `minute` (IST), each once, in the schedule's order."""
    at = minute.astimezone(IST)
    return [name for name, crons in schedule if any(matches(c, at) for c in crons)]


Opener = Callable[[urllib.request.Request, float], Any]


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 (our API's https URL)


def call(
    api_url: str,
    token: str,
    name: str,
    minute: datetime,
    *,
    opener: Opener = _open,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """POST one job for `minute`; tries again while the host's edge refuses. Returns the outcome
    (`ok`, `failed` for the app's own error answers, `gave_up`) and logs one line per attempt."""
    body = json.dumps({"scheduled_for": minute.isoformat()}).encode()
    stamp = f"{minute.astimezone(IST):%m-%d %H:%M}"
    for attempt in range(len(RETRY_AFTER_S) + 1):
        request = urllib.request.Request(  # noqa: S310 (our API's https URL)
            f"{api_url}/v1/internal/jobs/{name}",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with opener(request, CALL_TIMEOUT_S) as response:
                answer = json.loads(response.read() or b"{}")
            took = time.monotonic() - started
            summary = json.dumps(answer.get("summary"), separators=(",", ":"))[:200]
            log(stamp, name, "ok", answer.get("status"), f"{took:.1f}s", summary)
            return "ok"
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_CODES:
                detail = error.read()[:200].decode(errors="replace")
                log(stamp, name, "failed", error.code, detail)
                return "failed"
            reason = f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            reason = type(error).__name__ + f" {getattr(error, 'reason', '')}".rstrip()
        if attempt == len(RETRY_AFTER_S):
            log(stamp, name, "gave_up", reason, f"after {attempt + 1} tries")
            return "gave_up"
        log(stamp, name, "retry", reason, f"in {RETRY_AFTER_S[attempt]}s")
        sleep(RETRY_AFTER_S[attempt])
    return "gave_up"  # unreachable; keeps type checkers content


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _last_dispatched() -> datetime | None:
    try:
        return datetime.fromisoformat(STATE.read_text().strip())
    except (OSError, ValueError):
        return None


def _remember(minute: datetime) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(minute.isoformat())


def _ping(suffix: str = "") -> None:
    url = os.environ.get("HC_PING_URL")
    if not url:
        return
    with contextlib.suppress(urllib.error.URLError, TimeoutError, ConnectionError):
        urllib.request.urlopen(url + suffix, timeout=10).close()  # noqa: S310 (our ping URL)


def _pull() -> None:
    branch = subprocess.run(  # noqa: S603 (fixed arguments)
        ["git", "-C", str(ROOT), "branch", "--show-current"],  # noqa: S607 (git on PATH)
        capture_output=True,
        text=True,
    ).stdout.strip()
    pulled = subprocess.run(  # noqa: S603 (fixed arguments)
        ["git", "-C", str(ROOT), "pull", "-q", "--ff-only", "origin", branch or "main"],  # noqa: S607
        capture_output=True,
        text=True,
    )
    if pulled.returncode != 0:
        log("pull_failed", pulled.stderr.strip()[:200])


def run(api_url: str, token: str) -> int:
    me = Path(__file__).resolve()
    started_as = _digest(me)
    schedule = load()
    log("started", f"{len(schedule)} vm jobs", api_url)
    last = _last_dispatched()
    now = datetime.now(IST)
    minute = now.replace(second=0, microsecond=0)
    if (last is not None and last >= minute) or now.second >= LATE_START_S:
        minute += timedelta(minutes=1)  # done already, or too late in it to start now
    restart = False
    while True:
        time.sleep(max(0.0, (minute - datetime.now(IST)).total_seconds()))
        if restart and threading.active_count() == 1:
            log("restarting for new code")
            return 0
        try:
            schedule = load()
        except Exception as error:  # a broken pull keeps the last good schedule
            log("schedule_unreadable", type(error).__name__, str(error)[:200])
        names = due(schedule, minute)
        for name in names:
            threading.Thread(
                target=_dispatch, args=(api_url, token, name, minute), daemon=True
            ).start()
        _remember(minute)
        if minute.minute == 0:
            _ping()
        if minute.minute % PULL_EVERY_MIN == 7:
            _pull()
            restart = restart or _digest(me) != started_as
        minute += timedelta(minutes=1)


def _dispatch(api_url: str, token: str, name: str, minute: datetime) -> None:
    if call(api_url, token, name, minute) == "gave_up":
        _ping("/fail")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the `vm` jobs of infra/jobs/schedule.yaml.")
    parser.add_argument("--due", help="list the jobs due at this IST minute, e.g. 2026-11-05T21:00")
    args = parser.parse_args(argv)
    if args.due:
        minute = datetime.fromisoformat(args.due).replace(tzinfo=IST)
        print("\n".join(due(load(), minute)) or "(none)")
        return 0
    api_url = os.environ.get("API_URL", "").rstrip("/")
    token = os.environ.get("JOBS_TOKEN", "")
    if not api_url or not token:
        print("API_URL and JOBS_TOKEN are needed (scripts/jobs-on-vm.sh env)", file=sys.stderr)
        return 2
    return run(api_url, token)


if __name__ == "__main__":
    sys.exit(main())
