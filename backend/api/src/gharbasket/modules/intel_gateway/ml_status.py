"""Is the ML service up, and which models does it serve? (M10 §3.10; the AI health page, M12.)

One token-protected `GET /health/ai`, within `TIMEOUT_S` in all (a hard bound), never raising:
whatever goes wrong
(no connection, a slow or sleeping free instance, a refused token, a 5xx, a body that is not
what it should be) is `down`. It does not touch the call paths' circuit breakers; customers are
never waiting on it. The answer is kept in the process for `CACHE_S`, so an admin refreshing
the page does not wake or hammer the service.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx2
import structlog

from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.intel_gateway")

TIMEOUT_S = 3.0
CACHE_S = 60.0


@dataclass(frozen=True)
class ServedModel:
    model: str
    version: str


@dataclass(frozen=True)
class RegistrySync:
    """The ML service's last sync of promoted models from the registry (R2)."""

    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class MlServiceStatus:
    status: Literal["up", "down", "not_configured"]
    served: list[ServedModel]
    registry_sync: RegistrySync | None
    checked_at: datetime


@dataclass
class _Cache:
    key: tuple[str, bool] | None = None
    at: float = 0.0
    value: MlServiceStatus | None = None


_cache = _Cache()
# Replaced by tests that need to move time.
clock: Callable[[], float] = time.monotonic


def reset_cache() -> None:
    _cache.key, _cache.at, _cache.value = None, 0.0, None


def _moment(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _parse(body: Any, checked_at: datetime) -> MlServiceStatus:
    """ValueError, KeyError, TypeError or AttributeError for a body that is not /health/ai's."""
    served = [ServedModel(str(m["name"]), str(m["version"])) for m in body["models"]]
    sync = body.get("registry_sync")
    registry_sync = (
        RegistrySync(
            _moment(sync.get("last_success_at")),
            _moment(sync.get("last_failure_at")),
            sync["last_error"] if isinstance(sync.get("last_error"), str) else None,
        )
        if isinstance(sync, dict) and sync.get("enabled") is True
        else None
    )
    return MlServiceStatus("up", served, registry_sync, checked_at)


async def _probe(http: httpx2.AsyncClient, settings: Settings) -> MlServiceStatus:
    now = datetime.now(UTC)
    if not settings.ml_service_url:
        return MlServiceStatus("not_configured", [], None, now)
    headers = {}
    if settings.ml_service_token is not None:
        headers["Authorization"] = f"Bearer {settings.ml_service_token.get_secret_value()}"
    try:
        async with asyncio.timeout(TIMEOUT_S):  # a hard bound: httpx's timeouts are per phase
            response = await http.get(
                f"{settings.ml_service_url.rstrip('/')}/health/ai",
                headers=headers,
                timeout=TIMEOUT_S,
            )
        response.raise_for_status()
        return _parse(response.json(), now)
    except (
        httpx2.HTTPError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        status = exc.response.status_code if isinstance(exc, httpx2.HTTPStatusError) else None
        log.warning(
            "ml_health_down",
            reason=type(exc).__name__,
            **({"status": status} if status is not None else {}),
        )
        return MlServiceStatus("down", [], None, now)


async def ml_service_status(http: httpx2.AsyncClient, settings: Settings) -> MlServiceStatus:
    """The ML service's state for the admin console; the last answer if it is under a minute old."""
    key = (settings.ml_service_url or "", settings.ml_service_token is not None)
    if _cache.value is not None and _cache.key == key and clock() - _cache.at < CACHE_S:
        return _cache.value
    try:
        value = await _probe(http, settings)
    except Exception as exc:  # the page must always render; this never raises
        log.warning("ml_health_failed", reason=type(exc).__name__)
        value = MlServiceStatus("down", [], None, datetime.now(UTC))
    _cache.key, _cache.at, _cache.value = key, clock(), value
    return value
