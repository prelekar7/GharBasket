"""infra/jobs/schedule.yaml is the one schedule of every job (ADR-0021).

It must name every registered job exactly once, hold only crons that parse, and agree with the
literal `on.schedule` lines of the GitHub workflows that run the `actions` entries: staging's and
production's (ADR-0036), which run the same entries.
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
WORKFLOWS = [
    ROOT / ".github" / "workflows" / f"jobs-{target}.yml" for target in ("staging", "prod")
]
RUNNERS = {"vm", "actions", "manual"}
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


def workflow_crons(workflow: Path) -> list[str]:
    # PyYAML reads the key `on` as True (YAML 1.1).
    parsed = yaml.safe_load(workflow.read_text())
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


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda w: w.name)
def test_the_actions_crons_and_the_workflow_schedule_agree(workflow: Path) -> None:
    wanted = {cron for e in entries() if e["runner"] == "actions" for cron in crons_of(e)}
    literal = workflow_crons(workflow)
    assert [c for c, count in Counter(literal).items() if count > 1] == []
    assert sorted(wanted - set(literal)) == [], "actions crons missing from on.schedule"
    assert sorted(set(literal) - wanted) == [], "on.schedule lines no actions entry uses"


def test_production_runs_from_its_own_environment_and_secrets() -> None:
    """jobs-prod.yml must not read staging's repository secrets: a missing production secret
    would otherwise fall back to staging's (handbook 12 §12.9)."""
    text = WORKFLOWS[1].read_text()
    [job] = yaml.safe_load(text)["jobs"].values()
    assert job["environment"] == "production-ops"
    assert {"PROD_API_URL", "JOBS_TOKEN", "HC_OPS_URL"} <= set(job["env"])
    for name in ("STAGING_API_URL", "secrets.JOBS_TOKEN", "secrets.HC_OPS_URL"):
        assert name not in text, f"jobs-prod.yml reads {name}"


def test_a_cron_has_one_spelling() -> None:
    """`03 22 * * *` and `3 22 * * *` are the same time; the workflow matches strings."""
    for entry in entries():
        for cron in crons_of(entry):
            assert cron == " ".join(str(int(p)) if p.isdigit() else p for p in cron.split())


def awake_hours_per_day() -> float:
    """Average hours a day Neon computes, if each call keeps it awake 1 + 5 minutes (IST days).

    A rough model: it ignores people, deploys and cold starts. It exists to catch a cron that
    quietly puts Neon back on during the day. Weekly jobs count on their weekday.
    """
    awake = {weekday: [False] * 24 * 60 for weekday in range(7)}  # 0 = Sunday
    for entry in entries():
        for cron in crons_of(entry):
            minutes, hours, _, _, weekdays = parse(cron)
            for weekday in {d % 7 for d in weekdays}:
                for hour in hours:
                    for minute in minutes:
                        start = hour * 60 + minute
                        day = weekday
                        if entry["runner"] == "actions":  # UTC to IST, which may be the next day
                            start += IST_OFFSET
                            day = (weekday + start // (24 * 60)) % 7
                            start %= 24 * 60
                        for tick in range(start, start + RUN_MINUTES + IDLE_MINUTES):
                            awake[(day + tick // (24 * 60)) % 7][tick % (24 * 60)] = True
    return sum(sum(day) for day in awake.values()) / 7 / 60


def test_neon_stays_inside_its_free_compute_budget() -> None:
    hours = awake_hours_per_day()
    print(f"\nNeon awake about {hours:.1f} h a day = {hours * 0.25 * 30:.0f} CU-h a month")
    # Free tier: 100 CU-hours a month, and the compute is suspended when they run out. At a fixed
    # 0.25 CU that is 13.3 h a day; the schedule gets 8.1 h (61 CU-hours) so that people, deploys
    # and the journey script have the rest. 8.1 since refund_unassigned_tiffins got an 11:00 wake
    # for the lunch slots that close after 09:30's (M8 §6).
    assert hours <= 8.1


def test_every_lunch_slots_close_is_refunded_within_the_hour() -> None:
    """A lunch slot (start before 16:00) closes manual assignment 3 h before it starts (M8 §6:
    ready_by is start - 60 min, manual assignment closes 2 h before that), and the refund must
    follow at once, not at 18:00. Slots start on the half hour."""
    [entry] = [e for e in entries() if e["name"] == "refund_unassigned_tiffins"]
    runs = sorted(
        hour * 60 + minute
        for cron in crons_of(entry)
        for minutes, hours, *_ in [parse(cron)]
        for hour in hours
        for minute in minutes
    )
    for start in range(12 * 60 + 30, 16 * 60, 30):
        close = start - 3 * 60
        wait = min(run - close for run in runs if run >= close)
        assert wait <= 60, f"the lunch slot at {start // 60}:{start % 60:02d} waits {wait} min"


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
