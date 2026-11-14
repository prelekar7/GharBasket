"""scripts/neon_usage_check.py warns before staging's Neon project runs out of free compute hours;
these tests drive it against a fake Neon API, without the network or a key."""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "neon_usage_check", ROOT / "scripts" / "neon_usage_check.py"
)
assert _spec and _spec.loader
check: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

DIRECT = "postgresql://app:secret-pw@ep-cool-name-a1b2.ap-southeast-1.aws.neon.tech/neondb"
START = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)


def fake(cu_seconds: int, start: str = "2026-11-01T00:00:00Z", status: int = 200) -> Any:
    def call(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        assert method == "GET" and body is None
        if path == "/projects/gb-2/endpoints/ep-cool-name-a1b2":
            return 200, {"endpoint": {"branch_id": "br-main"}}
        if path == "/projects/gb-2":
            if status != 200:
                return status, {"message": "nope"}
            return 200, {
                "project": {"compute_time_seconds": cu_seconds, "consumption_period_start": start}
            }
        raise AssertionError(path)

    return call


def test_reads_cu_hours_and_the_next_reset() -> None:
    u = check.usage(fake(36 * 3600), DIRECT, "gb-2")
    assert u.project_id == "gb-2"
    assert u.cu_hours == 36
    assert u.period_start == START
    assert u.period_end == datetime(2026, 12, 1, tzinfo=UTC)


def test_the_reset_is_clamped_to_a_short_month() -> None:
    jan_31, dec_15 = datetime(2027, 1, 31, tzinfo=UTC), datetime(2026, 12, 15, tzinfo=UTC)
    assert check._next_month(jan_31) == datetime(2027, 2, 28, tzinfo=UTC)
    assert check._next_month(dec_15) == datetime(2027, 1, 15, tzinfo=UTC)


def usage(hours: float) -> Any:
    return check.Usage("gb-2", hours, START, datetime(2026, 12, 1, tzinfo=UTC))


def test_quiet_under_the_threshold_and_on_pace() -> None:
    # 14 days into a 30-day period: 40 h is on pace for about 86 h.
    assert check.problems(usage(40), datetime(2026, 11, 15, tzinfo=UTC), 100, 80) == []


def test_warns_at_the_threshold() -> None:
    [line] = check.problems(usage(80), datetime(2026, 11, 28, tzinfo=UTC), 100, 80)
    assert "80.0 of 100 CU-hours used (80 %)" in line
    assert "01 Dec" in line


def test_warns_when_the_pace_would_cross_the_limit() -> None:
    # 10 days in, 40 h used: about 120 h by the reset.
    [line] = check.problems(usage(40), datetime(2026, 11, 11, tzinfo=UTC), 100, 80)
    assert "on pace for 120 of 100" in line


def test_no_pace_warning_in_the_first_days() -> None:
    # Two days in, 10 h would project to 150 h, but two days are too few to judge.
    assert check.problems(usage(10), datetime(2026, 11, 3, tzinfo=UTC), 100, 80) == []


def test_main_exits_1_and_prints_no_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check, "http_call", lambda key: fake(90 * 3600))
    env = {"NEON_API_KEY": "napi_secret", "DATABASE_URL_DIRECT": DIRECT, "NEON_PROJECT_ID": "gb-2"}
    assert check.main(env, now=datetime(2026, 11, 20, tzinfo=UTC)) == 1
    out = capsys.readouterr()
    assert "90.0 of 100 CU-hours used" in out.out
    assert "napi_secret" not in out.out + out.err
    assert "secret-pw" not in out.out + out.err


def test_main_exits_0_when_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "http_call", lambda key: fake(5 * 3600))
    env = {"NEON_API_KEY": "k", "DATABASE_URL_DIRECT": DIRECT, "NEON_PROJECT_ID": "gb-2"}
    assert check.main(env, now=datetime(2026, 11, 20, tzinfo=UTC)) == 0


def test_main_honours_the_limit_and_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "http_call", lambda key: fake(30 * 3600))
    env = {
        "NEON_API_KEY": "k",
        "DATABASE_URL_DIRECT": DIRECT,
        "NEON_PROJECT_ID": "gb-2",
        "NEON_CU_HOURS_LIMIT": "50",
        "NEON_CU_HOURS_WARN_PCT": "50",
    }
    assert check.main(env, now=datetime(2026, 11, 28, tzinfo=UTC)) == 1


def test_main_exits_1_when_neon_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check, "http_call", lambda key: fake(0, status=401))
    env = {"NEON_API_KEY": "k", "DATABASE_URL_DIRECT": DIRECT, "NEON_PROJECT_ID": "gb-2"}
    assert check.main(env) == 1
    assert "Neon answered 401" in capsys.readouterr().err


def test_main_needs_the_key_and_url() -> None:
    assert check.main({}) == 2


def test_main_exits_1_on_a_network_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def down(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        raise OSError("no route to host")

    monkeypatch.setattr(check, "http_call", lambda key: down)
    env = {"NEON_API_KEY": "k", "DATABASE_URL_DIRECT": DIRECT, "NEON_PROJECT_ID": "gb-2"}
    assert check.main(env) == 1
    assert "no route to host" in capsys.readouterr().err
