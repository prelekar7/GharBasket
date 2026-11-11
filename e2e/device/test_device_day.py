"""Smoke tests of the device-day tool that need no services: its safety guard and its settings.

    uv run --directory e2e/backend pytest ../device

`DEVICE_DAY_RUN=1` also builds the whole day once (about a minute; needs `just up`, and
`just up-storage` for the photo uploads) and checks the summary.
"""

import asyncio
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

import device_day
import pytest
from device_day import DATABASE, DeviceConfig

import gharbasket.modules.catalog.tiffin_review as tiffin_review
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
    before = (subs_service.now, orders_service.now, kitchens.now, tiffin_review.now)
    clock = device_day.Clock()
    when = datetime(2026, 10, 30, 15, 30, tzinfo=UTC)
    with clock.installed():
        clock.at = when
        assert subs_service.now() == orders_service.now() == kitchens.now() == when
        assert tiffin_review.now() == when
    assert (subs_service.now, orders_service.now, kitchens.now, tiffin_review.now) == before
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


def test_whatsapp_stays_off_even_with_its_settings_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # As under `infisical run --env=staging -- just device-day`.
    monkeypatch.setenv("WHATSAPP_TOKEN", "live-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify")
    assert not config().settings(quiet=True).whatsapp_enabled


def test_a_busy_port_stops_the_build_before_anything_is_dropped() -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(SystemExit, match="in use"):
            device_day.ensure_port_free(config(port=port))
    device_day.ensure_port_free(config(port=port))  # free again: no error


def test_pack_refuses_an_api_that_is_not_this_tools_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(device_day, "SERVER_FILE", tmp_path / "server.json")
    with socket.socket() as other:  # some other API on the port, e.g. `just dev`
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]
        with pytest.raises(SystemExit, match="No device-day server"):
            device_day.ensure_device_server(config(port=port))
        # This tool's server marks itself; then pack may go on.
        device_day.mark_server(config(port=port))
        device_day.ensure_device_server(config(port=port))
        # A mark left by a server on another port is not this one.
        with pytest.raises(SystemExit):
            device_day.ensure_device_server(config(port=port + 1))


def test_a_lan_host_is_reached_on_loopback_and_warned_about() -> None:
    lan = config(host="0.0.0.0")  # noqa: S104 (a config value; nothing binds in this test)
    assert lan.client_host == "127.0.0.1" and not lan.loopback
    assert any("anyone on this Wi-Fi" in line for line in device_day.connection_lines(lan))
    assert not any("Wi-Fi" in line for line in device_day.connection_lines(config()))
