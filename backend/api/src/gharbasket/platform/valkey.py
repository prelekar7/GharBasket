"""Valkey client (Redis protocol).

Used for rate limits, the session deny-list, caching and, later, the Taskiq queue.
"""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis


def create_valkey(url: str) -> Redis:
    # Short timeouts: a slow Valkey must fail fast instead of holding up requests.
    return Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)


def _app_valkey(request: Request) -> Redis | None:
    return getattr(request.app.state, "valkey", None)


# The app's client, or None when VALKEY_URL is not set (callers then skip what needs it).
Valkey = Annotated[Redis | None, Depends(_app_valkey)]
