"""scripts/jobs_scheduler.py calls the `vm` entries of infra/jobs/schedule.yaml from the staging VM
(ADR-0032); these tests cover which jobs a minute calls and how a call is retried, without the
network."""

import importlib.util
import io
import json
import urllib.error
import urllib.request
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "jobs_scheduler", ROOT / "scripts" / "jobs_scheduler.py"
)
assert _spec and _spec.loader
scheduler: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scheduler)

IST = scheduler.IST


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=IST)


@pytest.mark.parametrize(
    ("minute", "jobs"),
    [
        # The cut-off minute: the lock with the evening window's minute-level jobs.
        (
            "2026-11-05T21:00",
            [
                "outbox_sweep",
                "build_data_export",
                "cutoff_lock",
                "send_refunds",
                "send_deliveries",
                "refresh_analytics",
                "expire_proposals",
            ],
        ),
        ("2026-11-05T21:05", ["outbox_sweep", "send_refunds", "send_deliveries"]),
        ("2026-11-05T14:00", []),  # between the windows Neon sleeps
        ("2026-11-08T18:00", ["generate_weekly_plans", "refund_unassigned_tiffins"]),
        ("2026-11-05T06:01", ["persist_gps_pings", "send_deliveries"]),
    ],
)
def test_a_minute_calls_the_jobs_the_schedule_names_for_it_in_ist(
    minute: str, jobs: list[str]
) -> None:
    assert scheduler.due(scheduler.load(), at(minute)) == jobs


def test_the_minute_is_read_in_ist_whatever_zone_it_comes_in() -> None:
    utc = datetime.fromisoformat("2026-11-05T15:30:00+00:00")  # 21:00 IST
    assert "cutoff_lock" in scheduler.due(scheduler.load(), utc)


def test_only_vm_entries_are_called() -> None:
    names = {entry.name for entry in scheduler.load()}
    assert "cutoff_lock" in names
    assert "compute_vendor_scores" not in names  # an `actions` entry: GitHub calls it


@pytest.mark.parametrize(
    ("cron", "minute", "hit"),
    [
        ("0-45/5 8 * * *", "2026-11-05T08:45", True),
        ("0-45/5 8 * * *", "2026-11-05T08:50", False),
        ("40 20 * * 0", "2026-11-08T20:40", True),  # Sunday
        ("40 20 * * 7", "2026-11-08T20:40", True),  # Sunday written as 7
        ("40 20 * * 0", "2026-11-09T20:40", False),
        ("0 9 1 * 1", "2026-11-09T09:00", True),  # both day fields: either one is enough
        ("0 9 1 * 1", "2026-11-01T09:00", True),
        ("0 9 1 * 1", "2026-11-03T09:00", False),
        ("5/20 * * * *", "2026-11-05T10:45", True),
    ],
)
def test_crons_match_as_cron_would(cron: str, minute: str, hit: bool) -> None:
    assert scheduler.matches(cron, at(minute)) is hit


def test_a_cron_that_does_not_parse_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "schedule.yaml"
    broken.write_text("jobs:\n  - {name: x, runner: vm, cron: '60 * * * *'}\n")
    with pytest.raises(ValueError, match="outside"):
        scheduler.load(broken)


class Answers:
    """A fake HTTP opener that answers from a list, remembering each request."""

    def __init__(self, *answers: int | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if answer != 200:
            raise urllib.error.HTTPError(
                request.full_url,
                answer,
                "x",
                Message(),
                io.BytesIO(b'{"code":"platform.job_failed"}'),
            )
        body = {"status": "ok", "summary": {"sent": 0}}
        return io.BytesIO(json.dumps(body).encode())


def run_call(opener: Answers, waits: list[float]) -> str:
    outcome, _ = scheduler.call(
        "https://api.test", "t" * 40, "cutoff_lock", at("2026-11-05T21:00"),
        opener=opener, sleep=waits.append,
    )  # fmt: skip
    return outcome


def test_a_call_posts_the_minute_with_the_token() -> None:
    opener, waits = Answers(200), []
    assert run_call(opener, waits) == "ok" and waits == []
    request = opener.requests[0]
    assert request.full_url == "https://api.test/v1/internal/jobs/cutoff_lock"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer " + "t" * 40
    assert json.loads(cast(bytes, request.data)) == {"scheduled_for": "2026-11-05T21:00:00+05:30"}


def test_the_hosts_refusals_are_tried_again_with_the_same_minute() -> None:
    opener, waits = Answers(503, 429, urllib.error.URLError("reset"), 200), []
    assert run_call(opener, waits) == "ok"
    assert waits == list(scheduler.RETRY_AFTER_S)
    assert {r.data for r in opener.requests} == {opener.requests[0].data}


def test_after_the_last_try_the_call_gives_up() -> None:
    opener, waits = Answers(503, 503, 503, TimeoutError()), []
    assert run_call(opener, waits) == "gave_up"
    assert len(opener.requests) == len(scheduler.RETRY_AFTER_S) + 1


@pytest.mark.parametrize("code", [500, 401, 404])
def test_the_apps_own_errors_are_final(code: int) -> None:
    opener, waits = Answers(code), []
    assert run_call(opener, waits) == "failed"
    assert waits == [] and len(opener.requests) == 1


# --- the alerts (ADR-0033, M0 §6.1) -------------------------------------------------------------

HC = "https://hc-ping.test/"


def dispatch(
    name: str, heartbeat: str | None, *answers: int | Exception
) -> list[urllib.request.Request]:
    """Dispatch one job with the given answers, then a 200 for every ping; returns the pings."""
    opener = Answers(*answers, 200, 200)
    scheduler.dispatch(
        "https://api.test", "t" * 40, name, at("2026-11-05T21:00"), heartbeat,
        opener=opener, sleep=lambda _: None,
    )  # fmt: skip
    return [r for r in opener.requests if r.full_url.startswith(HC)]


@pytest.fixture
def ping_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("HC_PING_URL", "HC_CUTOFF_URL", "HC_OPS_URL"):
        monkeypatch.setenv(variable, HC + variable.lower())


@pytest.mark.usefixtures("ping_urls")
def test_a_job_that_answers_ok_pings_its_heartbeat_only() -> None:
    pings = dispatch("cutoff_lock", "HC_CUTOFF_URL", 200)
    assert [(p.full_url, p.get_method()) for p in pings] == [(HC + "hc_cutoff_url", "GET")]
    assert dispatch("send_refunds", None, 200) == []


@pytest.mark.usefixtures("ping_urls")
@pytest.mark.parametrize(
    ("answers", "outcome"),
    [((500,), 'failed HTTP 500 {"code":"platform.job_failed"}'), ((503,) * 4, "gave_up HTTP 503")],
)
def test_a_job_that_fails_or_gives_up_tells_the_ops_check(
    answers: tuple[int, ...], outcome: str
) -> None:
    pings = dispatch("cutoff_lock", "HC_CUTOFF_URL", *answers)
    assert [(p.full_url, p.get_method()) for p in pings] == [(HC + "hc_ops_url/fail", "POST")]
    body = cast(bytes, pings[0].data).decode()
    assert body.startswith(f"cutoff_lock 2026-11-05 21:00 IST {outcome}")


def test_unset_ping_urls_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HC_OPS_URL", raising=False)
    monkeypatch.delenv("HC_CUTOFF_URL", raising=False)
    assert dispatch("cutoff_lock", "HC_CUTOFF_URL", 500) == []
    assert dispatch("cutoff_lock", "HC_CUTOFF_URL", 200) == []


def test_a_ping_that_fails_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_PING_URL", HC + "x")
    opener = Answers(urllib.error.URLError("down"))
    scheduler.ping("HC_PING_URL", opener=opener)
    assert len(opener.requests) == 1


def test_the_cutoff_and_the_watchdog_have_heartbeats() -> None:
    heartbeats = {e.name: e.heartbeat for e in scheduler.load() if e.heartbeat}
    assert heartbeats == {"cutoff_lock": "HC_CUTOFF_URL", "ops_check": "HC_OPS_URL"}


def test_a_heartbeat_must_name_an_hc_variable(tmp_path: Path) -> None:
    broken = tmp_path / "schedule.yaml"
    broken.write_text("jobs:\n  - {name: x, runner: vm, cron: '0 * * * *', heartbeat: PATH}\n")
    with pytest.raises(ValueError, match="heartbeat"):
        scheduler.load(broken)


def test_due_on_the_command_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert scheduler.main(["--due", "2026-11-05T21:05"]) == 0
    assert capsys.readouterr().out.split() == ["outbox_sweep", "send_refunds", "send_deliveries"]


def test_running_needs_the_api_url_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.delenv("JOBS_TOKEN", raising=False)
    assert scheduler.main([]) == 2
