"""Create, update and delete the cron-job.org jobs from infra/jobs/schedule.yaml (ADR-0021, D25).

Every `cron-job.org` entry becomes one cron-job.org job per cron line, titled
`gharbasket-staging <name>` (with ` #<n>` when the entry has several lines). Each one POSTs `{}`
to `<api>/v1/internal/jobs/<name>` with the operator token, so the job runs for "now", in the
timezone Asia/Kolkata (the schedule's crons are IST). Jobs whose title starts with the prefix are
ours: a sync makes them match the schedule and deletes the ones the schedule no longer has. Jobs
with other titles are never touched.

    infisical run --env=staging -- just cronjob-sync            # show the plan
    infisical run --env=staging -- just cronjob-sync --apply    # make the changes

Needs CRONJOB_ORG_API_KEY (cron-job.org Console → Settings → API) and JOBS_TOKEN; STAGING_API_URL
is optional.

The free API allows 100 requests a day and 5 job creations a minute: the first sync (one list and
75 creations) takes about 15 minutes and most of a day's requests. Later syncs only touch what
changed. The list cannot show a job's headers, so a new JOBS_TOKEN needs `--rotate-token`, which
updates every job.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:  # python.org's macOS Python has no root certificates of its own
    import certifi

    TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    TLS = ssl.create_default_context()

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / "infra" / "jobs" / "schedule.yaml"
API = "https://api.cron-job.org"
PREFIX = "gharbasket-staging "
STAGING_API_URL = "https://gharbasket-api.onrender.com"  # public; STAGING_API_URL overrides it
TIMEZONE = "Asia/Kolkata"
POST = 1  # cron-job.org's requestMethod enum
TIMEOUT_S = 30  # the free plan's longest request timeout
# (schedule key, low, high) of the five cron fields, in cron order.
FIELDS = [
    ("minutes", 0, 59),
    ("hours", 0, 23),
    ("mdays", 1, 31),
    ("months", 1, 12),
    ("wdays", 0, 7),
]
# Seconds to wait after each call: creations are limited to 5 a minute, the rest to 5 a second.
PAUSE = {"PUT": 12.5, "PATCH": 0.25, "DELETE": 0.25, "GET": 0.25}


def field_values(text: str, low: int, high: int) -> list[int]:
    """One cron field as cron-job.org's list: `[-1]` for `*`, else the sorted values it names
    (`a`, `a-b`, `*/n`, `a-b/n` and comma lists of them)."""
    if text == "*":
        return [-1]
    values: set[int] = set()
    for part in text.split(","):
        span, _, step = part.partition("/")
        if span == "*":
            start, end = low, high
        elif "-" in span:
            start, end = (int(x) for x in span.split("-"))
        else:
            start = end = int(span)
        if not (low <= start <= end <= high):
            raise ValueError(f"cron field {text!r} is outside {low}-{high}")
        values.update(range(start, end + 1, int(step) if step else 1))
    return sorted(values)


def schedule_of(cron: str) -> dict[str, Any]:
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"cron {cron!r} needs 5 fields")
    out: dict[str, Any] = {"timezone": TIMEZONE, "expiresAt": 0}
    for (key, low, high), text in zip(FIELDS, parts, strict=True):
        out[key] = field_values(text, low, high)
    if out["wdays"] != [-1]:
        out["wdays"] = sorted({0 if d == 7 else d for d in out["wdays"]})  # 7 is Sunday too
        if out["mdays"] != [-1]:
            # Cron runs when either matches; cron-job.org would need both.
            raise ValueError(f"cron {cron!r} restricts both day fields")
    return out


def desired_jobs(api_url: str, token: str, schedule: Path = SCHEDULE) -> dict[str, dict[str, Any]]:
    """Title → the cron-job.org job each `cron-job.org` entry of the schedule needs."""
    jobs: dict[str, dict[str, Any]] = {}
    for entry in yaml.safe_load(schedule.read_text())["jobs"]:
        if entry["runner"] != "cron-job.org":
            continue
        crons = [entry["cron"]] if isinstance(entry["cron"], str) else entry["cron"]
        for n, cron in enumerate(crons, start=1):
            title = PREFIX + entry["name"] + (f" #{n}" if len(crons) > 1 else "")
            jobs[title] = {
                "title": title,
                "url": f"{api_url}/v1/internal/jobs/{entry['name']}",
                "enabled": True,
                "saveResponses": True,
                "requestTimeout": TIMEOUT_S,
                "requestMethod": POST,
                "schedule": schedule_of(cron),
                "extendedData": {
                    "headers": {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    "body": "{}",
                },
                # A cold start can fail one call; three in a row is worth an email.
                "notification": {"onFailure": True, "onFailureCount": 3, "onDisable": True},
            }
    return jobs


def _same(have: dict[str, Any], want: dict[str, Any]) -> bool:
    """What the job list shows of a job (not its headers or body) matches what we want."""
    keys = ("url", "enabled", "saveResponses", "requestTimeout", "requestMethod")
    if any(have.get(k) != want[k] for k in keys):
        return False
    sched = have.get("schedule") or {}
    return (
        all(
            sorted(sched.get(k) or []) == want["schedule"][k]
            for k in ("minutes", "hours", "mdays", "months", "wdays")
        )
        and sched.get("timezone") == TIMEZONE
    )


@dataclass
class Plan:
    create: list[dict[str, Any]]
    update: list[tuple[int, dict[str, Any]]]
    delete: list[tuple[int, str]]

    def requests(self) -> int:
        return len(self.create) + len(self.update) + len(self.delete)


def plan(
    existing: list[dict[str, Any]], wanted: dict[str, dict[str, Any]], *, rotate_token: bool
) -> Plan:
    ours = {j["title"]: j for j in existing if str(j.get("title", "")).startswith(PREFIX)}
    out = Plan([], [], [])
    for title, job in wanted.items():
        have = ours.get(title)
        if have is None:
            out.create.append(job)
        elif rotate_token or not _same(have, job):
            out.update.append((have["jobId"], job))
    out.delete = [(j["jobId"], t) for t, j in ours.items() if t not in wanted]
    return out


class CronJobOrg:
    def __init__(self, key: str) -> None:
        self.key = key
        self.calls = 0

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(2):
            req = urllib.request.Request(  # noqa: S310 (a fixed https URL)
                API + path,
                data=data,
                method=method,
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            )
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=30, context=TLS) as resp:  # noqa: S310
                    raw = resp.read()
                time.sleep(PAUSE[method])
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as err:
                if err.code == 429 and attempt == 0:
                    print("  rate limited; waiting a minute")
                    time.sleep(61)
                    continue
                detail = err.read().decode(errors="replace")[:300]
                raise SystemExit(f"cron-job.org {method} {path}: {err.code} {detail}") from None
        raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="make the changes (default: plan only)"
    )
    parser.add_argument(
        "--rotate-token", action="store_true", help="update every job (after a new JOBS_TOKEN)"
    )
    parser.add_argument(
        "--budget", type=int, default=95, help="most API calls to spend (free plan: 100 a day)"
    )
    args = parser.parse_args()

    missing = [k for k in ("CRONJOB_ORG_API_KEY", "JOBS_TOKEN") if not os.environ.get(k)]
    if missing:
        print(f"Set {', '.join(missing)} (infisical run --env=staging -- ...)", file=sys.stderr)
        return 2
    api_url = os.environ.get("STAGING_API_URL", STAGING_API_URL).rstrip("/")
    wanted = desired_jobs(api_url, os.environ["JOBS_TOKEN"])
    client = CronJobOrg(os.environ["CRONJOB_ORG_API_KEY"])

    listing = client.call("GET", "/jobs")
    if listing.get("someFailed"):
        print("cron-job.org could not list every job; try again later", file=sys.stderr)
        return 1
    todo = plan(listing.get("jobs", []), wanted, rotate_token=args.rotate_token)

    print(f"{len(wanted)} jobs in the schedule for {api_url}")
    for job in todo.create:
        print(f"  create  {job['title']}")
    for _, job in todo.update:
        print(f"  update  {job['title']}")
    for _, title in todo.delete:
        print(f"  delete  {title}")
    needed = 1 + todo.requests()
    print(f"{todo.requests()} change(s), {needed} API call(s) with the listing")
    if not todo.requests() or not args.apply:
        return 0
    if needed > args.budget:
        print(
            f"More than --budget {args.budget} calls; raise it or run again tomorrow",
            file=sys.stderr,
        )
        return 1
    if todo.create:
        minutes = len(todo.create) * 13 // 60 + 1
        print(f"Creating {len(todo.create)} at 5 a minute: about {minutes} min")

    for job_id, title in todo.delete:
        client.call("DELETE", f"/jobs/{job_id}")
        print(f"  deleted {title}")
    for job_id, job in todo.update:
        client.call("PATCH", f"/jobs/{job_id}", {"job": job})
        print(f"  updated {job['title']}")
    for job in todo.create:
        job_id = client.call("PUT", "/jobs", {"job": job})["jobId"]
        print(f"  created {job['title']} ({job_id})")
    print(f"Done: {client.calls} API call(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
