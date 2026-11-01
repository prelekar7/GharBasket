"""Smoke tests of the device-day tool that need no services: its safety guard and its settings.

    uv run --directory e2e/backend pytest ../device

`DEVICE_DAY_RUN=1` also builds the whole day once (about a minute; needs `just up`, and
`just up-storage` for the photo uploads) and checks the summary.
"""

import asyncio
import os
from datetime import UTC, datetime

import device_day
import pytest
from device_day import DATABASE, DeviceConfig

import gharbasket.modules.orders.service as orders_service
import gharbasket.modules.partners.kitchens as kitchens
import gharbasket.modules.subscriptions.service as subs_service

LOCAL = "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432"
OTHERS = [
    "gharbasket",
    "gharbasket_test",
    "gharbasket_test_gw0",
    "gharbasket_e2e",
    "gharbasket_e2e_web",
    "gharbasket_e2e_web_seed",
    "gharbasket_device_old",  # only the exact name: no prefix, no suffix
    "gharbasket_devices",
    "postgres",
]


def config(**changes: object) -> DeviceConfig:
    base = DeviceConfig.from_env()
    values = {**base.__dict__, "database_url": f"{LOCAL}/{DATABASE}", **changes}
    return DeviceConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", OTHERS)
def test_the_guard_refuses_every_other_database(name: str) -> None:
    with pytest.raises(SystemExit, match="refusing database"):
        device_day.assert_device_database(f"{LOCAL}/{name}")
    with pytest.raises(SystemExit):
        config(database_url=f"{LOCAL}/{name}").check()


def test_the_guard_refuses_a_database_that_is_not_local() -> None:
    with pytest.raises(SystemExit, match="local Docker database"):
        device_day.assert_device_database(
            f"postgresql+asyncpg://u:p@db.example.com:5432/{DATABASE}"
        )


def test_the_guard_accepts_the_device_database() -> None:
    config().check()


@pytest.mark.parametrize("url", ["redis://localhost:6380/0", "redis://localhost:6380/15"])
def test_the_guard_refuses_other_valkey_databases(url: str) -> None:
    with pytest.raises(SystemExit, match="Valkey db"):
        config(valkey_url=url).check()


def test_the_guard_refuses_a_valkey_that_is_not_local() -> None:
    with pytest.raises(SystemExit, match="local Valkey"):
        config(valkey_url=f"redis://cache.example.com:6379/{device_day.VALKEY_DB}").check()


def test_the_drop_itself_refuses_when_called_with_another_database() -> None:
    """The drop re-checks the name: a caller that skipped `check()` still cannot reach it."""
    with pytest.raises(SystemExit, match="refusing database"):
        asyncio.run(device_day.recreate_database(f"{LOCAL}/gharbasket_test"))


@pytest.mark.parametrize("command", ["run", "prepare", "serve", "pack"])
def test_every_command_checks_before_it_touches_anything(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched: list[str] = []

    async def drop(url: str) -> None:
        touched.append(url)

    monkeypatch.setattr(device_day, "recreate_database", drop)
    monkeypatch.setenv("DEVICE_DATABASE_URL", f"{LOCAL}/gharbasket")
    with pytest.raises(SystemExit, match="refusing database 'gharbasket'"):
        device_day.main([command])
    assert touched == []


def test_the_api_is_set_up_for_the_phone() -> None:
    settings = config(jobs_token="t" * 20).settings()
    assert settings.app_env == "dev"
    assert settings.dev_login_enabled
    assert settings.firebase_project_id == "gharbasket-2f523"
    assert "https://localhost" in settings.cors_allow_origins
    assert settings.s3_endpoint_url == "http://localhost:8333"
    assert settings.jobs_token is not None and settings.jobs_token.get_secret_value() == "t" * 20
    assert not settings.fcm_enabled and not settings.razorpay_enabled
    assert settings.ml_service_url is None


def test_the_server_listens_on_loopback_unless_asked() -> None:
    assert config().host in ("127.0.0.1", os.environ.get("DEVICE_HOST"))


def test_the_module_clocks_read_the_step_and_are_put_back() -> None:
    before = (subs_service.now, orders_service.now, kitchens.now)
    clock = device_day.Clock()
    when = datetime(2026, 10, 30, 15, 30, tzinfo=UTC)
    with clock.installed():
        clock.at = when
        assert subs_service.now() == orders_service.now() == kitchens.now() == when
    assert (subs_service.now, orders_service.now, kitchens.now) == before
    assert abs((subs_service.now() - datetime.now(UTC)).total_seconds()) < 5


def test_job_times_are_ist_wall_clock() -> None:
    from datetime import date

    assert device_day.ist(date(2026, 11, 1), 5, 0).isoformat() == "2026-10-31T23:30:00+00:00"


@pytest.mark.skipif(os.environ.get("DEVICE_DAY_RUN") != "1", reason="DEVICE_DAY_RUN=1 builds it")
def test_the_whole_day_builds_and_leaves_the_morning() -> None:
    lines = asyncio.run(device_day.prepare(DeviceConfig.from_env()))
    text = "\n".join(lines)
    assert "Rider route" in text and "queued" in text and "GB1:" in text
    assert (device_day.OUT / "codes.txt").exists()
