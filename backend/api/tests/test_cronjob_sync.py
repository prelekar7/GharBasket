"""scripts/cronjob_sync.py turns the `cron-job.org` entries of infra/jobs/schedule.yaml into
cron-job.org jobs; these tests cover the translation and the plan, without the network."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("cronjob_sync", ROOT / "scripts" / "cronjob_sync.py")
assert _spec and _spec.loader
sync: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

API = "https://api.example.test"


def wanted() -> dict[str, dict[str, Any]]:
    return sync.desired_jobs(API, "t" * 40)


@pytest.mark.parametrize(
    ("text", "low", "high", "values"),
    [
        ("*", 0, 59, [-1]),
        ("15", 0, 59, [15]),
        ("6-7", 0, 23, [6, 7]),
        ("*/15", 0, 59, [0, 15, 30, 45]),
        ("30-59/10", 0, 59, [30, 40, 50]),
        ("0,15,30", 0, 59, [0, 15, 30]),
        ("9,12,14", 0, 23, [9, 12, 14]),
    ],
)
def test_field_values(text: str, low: int, high: int, values: list[int]) -> None:
    assert sync.field_values(text, low, high) == values


def test_schedule_is_ist_with_sunday_as_zero() -> None:
    out = sync.schedule_of("40 20 * * 7")
    assert out == {
        "timezone": "Asia/Kolkata",
        "expiresAt": 0,
        "minutes": [40],
        "hours": [20],
        "mdays": [-1],
        "months": [-1],
        "wdays": [0],
    }


@pytest.mark.parametrize("cron", ["0 21 * *", "61 * * * *", "0 0 1 * 1"])
def test_bad_crons_are_refused(cron: str) -> None:
    with pytest.raises(ValueError, match="cron"):
        sync.schedule_of(cron)


def test_one_job_per_cron_line_of_each_cron_job_org_entry() -> None:
    entries = yaml.safe_load(sync.SCHEDULE.read_text())["jobs"]
    lines = sum(
        1 if isinstance(e["cron"], str) else len(e["cron"])
        for e in entries
        if e["runner"] == "cron-job.org"
    )
    jobs = wanted()
    assert len(jobs) == lines
    names = {e["name"] for e in entries if e["runner"] != "cron-job.org"}
    assert not any(j["url"].rsplit("/", 1)[1] in names for j in jobs.values())


def test_a_job_posts_an_empty_body_with_the_token() -> None:
    job = wanted()["gharbasket-staging cutoff_lock"]
    assert job["url"] == f"{API}/v1/internal/jobs/cutoff_lock"
    assert job["requestMethod"] == 1
    assert job["extendedData"] == {
        "headers": {"Authorization": "Bearer " + "t" * 40, "Content-Type": "application/json"},
        "body": "{}",
    }
    assert job["schedule"]["minutes"] == [0]
    assert job["schedule"]["hours"] == [21]
    assert "gharbasket-staging outbox_sweep #8" in wanted()


def listed(job: dict[str, Any], job_id: int) -> dict[str, Any]:
    """A job as GET /jobs shows it: no headers, body or notification settings."""
    return {k: v for k, v in job.items() if k not in ("extendedData", "notification")} | {
        "jobId": job_id
    }


def test_first_sync_creates_every_job() -> None:
    todo = sync.plan([], wanted(), rotate_token=False)
    assert len(todo.create) == len(wanted())
    assert todo.update == todo.delete == []


def test_a_synced_account_needs_nothing_and_others_jobs_are_left_alone() -> None:
    existing = [listed(job, i) for i, job in enumerate(wanted().values())]
    existing.append({"jobId": 999, "title": "someone else's job", "url": "https://x.test"})
    todo = sync.plan(existing, wanted(), rotate_token=False)
    assert todo.requests() == 0


def test_changed_and_dropped_jobs_are_updated_and_deleted() -> None:
    jobs = wanted()
    existing = [listed(job, i) for i, job in enumerate(jobs.values())]
    cutoff = next(j for j in existing if j["title"] == "gharbasket-staging cutoff_lock")
    cutoff["schedule"] = cutoff["schedule"] | {"minutes": [5]}
    existing.append({"jobId": 500, "title": "gharbasket-staging retired_job"})
    todo = sync.plan(existing, jobs, rotate_token=False)
    assert [job["title"] for _, job in todo.update] == ["gharbasket-staging cutoff_lock"]
    assert todo.delete == [(500, "gharbasket-staging retired_job")]
    assert todo.create == []


def test_rotate_token_updates_every_job() -> None:
    existing = [listed(job, i) for i, job in enumerate(wanted().values())]
    todo = sync.plan(existing, wanted(), rotate_token=True)
    assert len(todo.update) == len(wanted())
