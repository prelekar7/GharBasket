"""infra/jobs/schedule.yaml is the one schedule of every job (ADR-0021).

It must name every registered job exactly once, hold only crons that parse, and agree with the
literal `on.schedule` lines of the GitHub workflow that runs the `actions` entries.
"""

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from gharbasket.modules.registry import load_modules
from gharbasket.platform.jobs import JOBS

ROOT = Path(__file__).resolve().parents[3]
SCHEDULE = ROOT / "infra" / "jobs" / "schedule.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "jobs-staging.yml"
RUNNERS = {"cron-job.org", "actions", "manual"}
# (low, high) of the five cron fields; day-of-week 7 is Sunday too, as in most crons.
FIELDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
# Neon: scales to zero after 5 idle minutes; a job call counts as 1 minute of work.
IDLE_MINUTES, RUN_MINUTES = 5, 1
IST_OFFSET = 5 * 60 + 30


def entries() -> list[dict[str, Any]]:
    return yaml.safe_load(SCHEDULE.read_text())["jobs"]


def crons_of(entry: dict[str, Any]) -> list[str]:
    cron = entry.get("cron")
    if cron is None:
        return []
    return [cron] if isinstance(cron, str) else list(cron)


def field_values(field: str, low: int, high: int) -> set[int]:
    """The values a cron field names: `*`, `a`, `a-b`, `*/n`, `a-b/n` and comma lists of them."""
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


def workflow_crons() -> list[str]:
    # PyYAML reads the key `on` as True (YAML 1.1).
    parsed = yaml.safe_load(WORKFLOW.read_text())
    return [item["cron"] for item in parsed[True]["schedule"]]


def test_every_job_has_one_entry_and_every_entry_a_job() -> None:
    load_modules()
    names = [e["name"] for e in entries()]
    assert [n for n, count in Counter(names).items() if count > 1] == []
    assert sorted(set(JOBS) - set(names)) == [], "jobs with no entry in schedule.yaml"
    assert sorted(set(names) - set(JOBS)) == [], "entries for jobs that do not exist"


def test_every_entry_has_a_runner_a_reason_and_crons_that_parse() -> None:
    for entry in entries():
        name = entry["name"]
        assert entry["runner"] in RUNNERS, name
        assert entry.get("why"), f"{name} needs a `why`"
        crons = crons_of(entry)
        if entry["runner"] == "manual":
            assert crons == [], f"{name} is manual and has no cron"
            continue
        assert crons, f"{name} needs a cron"
        for cron in crons:
            parse(cron)
        if entry["runner"] == "actions":
            assert len(crons) == 1, f"{name}: GitHub runs one cron per entry"
            assert entry.get("ist"), f"{name} needs `ist` for people"


def test_the_actions_crons_and_the_workflow_schedule_agree() -> None:
    wanted = {cron for e in entries() if e["runner"] == "actions" for cron in crons_of(e)}
    literal = workflow_crons()
    assert [c for c, count in Counter(literal).items() if count > 1] == []
    assert sorted(wanted - set(literal)) == [], "actions crons missing from on.schedule"
    assert sorted(set(literal) - wanted) == [], "on.schedule lines no actions entry uses"


def test_a_cron_has_one_spelling() -> None:
    """`03 22 * * *` and `3 22 * * *` are the same time; the workflow matches strings."""
    for entry in entries():
        for cron in crons_of(entry):
            assert cron == " ".join(str(int(p)) if p.isdigit() else p for p in cron.split())


def awake_minutes_per_day() -> int:
    """Minutes of Neon compute on the busiest weekday, if each call keeps it awake 1 + 5 minutes.

    A rough model: it ignores people, deploys and cold starts. It exists to catch a cron that
    quietly puts Neon back on all day.
    """
    awake = [False] * 24 * 60
    for entry in entries():
        for cron in crons_of(entry):
            minutes, hours, *_ = parse(cron)
            for hour in hours:
                for minute in minutes:
                    start = hour * 60 + minute
                    if entry["runner"] == "actions":
                        start = (start + IST_OFFSET) % (24 * 60)
                    # Weekly jobs are counted too: the busiest day is what a budget needs.
                    for tick in range(start, start + RUN_MINUTES + IDLE_MINUTES):
                        awake[tick % (24 * 60)] = True
    return sum(awake)


def test_neon_stays_inside_its_free_compute_budget() -> None:
    minutes = awake_minutes_per_day()
    hours = minutes / 60
    print(f"\nNeon awake about {hours:.1f} h a day = {hours * 0.25 * 30:.0f} CU-h a month")
    # Free tier: about 100 CU-hours a month at 0.25 CU, i.e. 13.3 awake hours a day. People and
    # deploys come on top, so the schedule alone may not pass 13.
    assert hours <= 13


@pytest.mark.parametrize(
    ("cron", "ok"),
    [
        ("*/5 5-9,18-23 * * *", True),
        ("0,15,30 0 * * *", True),
        ("* * * *", False),
        ("60 * * * *", False),
        ("* 24 * * *", False),
        ("*/0 * * * *", False),
    ],
)
def test_the_cron_reader_rejects_what_cron_would(cron: str, ok: bool) -> None:
    if ok:
        parse(cron)
    else:
        with pytest.raises(ValueError, match=r"fields|outside"):
            parse(cron)
