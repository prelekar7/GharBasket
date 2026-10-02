import json
import logging
from pathlib import Path

import pytest
import structlog

from gharbasket.platform.logging import configure_logging
from gharbasket.platform.settings import Settings
from gharbasket.tools.export_openapi import main as export_openapi


def test_cors_origins_read_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://localhost, capacitor://localhost")

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.cors_allow_origins == ["https://localhost", "capacitor://localhost"]


def test_unknown_env_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTGRES_USER", "someone")

    Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_logs_are_json_lines_with_request_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    structlog.contextvars.bind_contextvars(request_id="abc123")
    try:
        structlog.get_logger("gharbasket.test").info("order_locked", order_id=42)
        logging.getLogger("uvicorn.error").info("server started")
    finally:
        structlog.contextvars.clear_contextvars()

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    ours, uvicorns = lines

    assert ours["event"] == "order_locked"
    assert ours["order_id"] == 42
    assert ours["request_id"] == "abc123"
    assert ours["level"] == "info"
    assert "timestamp" in ours
    assert uvicorns["event"] == "server started"
    assert uvicorns["logger"] == "uvicorn.error"


def test_export_openapi_writes_the_contract(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    export_openapi([str(target)])

    spec = json.loads(target.read_text())
    assert spec["info"]["title"] == "GharBasket API"
    assert "/v1/health" in spec["paths"]
