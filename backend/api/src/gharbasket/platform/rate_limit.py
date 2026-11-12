"""Fixed-window rate limits in Valkey: INCR a per-window counter and give it an expiry.

Simple and enough for sign-in protection and per-user daily caps. Keys never contain personal
data (callers hash phone numbers). If Valkey is down the request is allowed: Firebase already
limits OTP sending, and a broken limiter must not stop people signing in.
"""

import hashlib
import time
from collections.abc import Mapping

import structlog
from fastapi import Request
from redis.asyncio import Redis

from gharbasket.platform.errors import ProblemError

log = structlog.get_logger("gharbasket.rate_limit")

# The pages anyone may read without signing in (`/v1/public/*`: M12's society page, M8's cook
# page) share one counter per IP and one cache header (sent on 200 only).
PUBLIC_PER_MINUTE = 60
PUBLIC_CACHE = "public, max-age=300, stale-while-revalidate=600"
NO_STORE = {"Cache-Control": "no-store"}  # a public page's 404 or 429 is never cached


def private_key(value: str) -> str:
    """A stable key part for personal data (phone numbers) that does not reveal it."""
    return hashlib.sha256(value.encode()).hexdigest()[:24]


async def enforce(
    valkey: Redis | None,
    name: str,
    *,
    limit: int,
    window_s: int,
    offset_s: int = 0,
    code: str = "auth.rate_limited",
    detail: str = "Too many attempts. Wait a little and try again.",
    headers: Mapping[str, str] | None = None,
) -> None:
    """Count one attempt for `name`; 429 with `code` once `limit` is passed.

    Windows start at the epoch shifted by `offset_s`: a cap per IST day passes 19800 (05:30)."""
    if valkey is None:
        return
    now = int(time.time()) + offset_s
    window = now // window_s
    key = f"gb:rl:{name}:{window}"
    try:
        async with valkey.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_s, nx=True)
            count, _ = await pipe.execute()
    except Exception as exc:
        log.warning("rate_limit_skipped", error=type(exc).__name__)
        return
    if int(count) > limit:
        retry_after = window_s - now % window_s
        raise ProblemError(
            429, code, detail, headers={**(headers or {}), "Retry-After": str(retry_after)}
        )


def client_ip(request: Request) -> str:
    # On Render, uvicorn runs with --proxy-headers, so this is the real caller's address.
    return request.client.host if request.client else "unknown"


async def enforce_public(valkey: Redis | None, request: Request) -> None:
    """Count one request to a `/v1/public/*` page against the caller's IP (60 a minute)."""
    await enforce(
        valkey,
        f"public:ip:{client_ip(request)}",
        limit=PUBLIC_PER_MINUTE,
        window_s=60,
        code="request.rate_limited",
        detail="Too many requests. Wait a little and try again.",
        headers=NO_STORE,
    )


def public_not_found(code: str, detail: str) -> ProblemError:
    """A public page's 404, sent with `Cache-Control: no-store`."""
    return ProblemError(404, code, detail, headers=NO_STORE)
