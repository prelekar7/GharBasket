"""Fixed-window rate limits in Valkey: INCR a per-window counter and give it an expiry.

Simple and enough for sign-in protection and per-user daily caps. Keys never contain personal
data (callers hash phone numbers). If Valkey is down the request is allowed: Firebase already
limits OTP sending, and a broken limiter must not stop people signing in.
"""

import hashlib
import time

import structlog
from redis.asyncio import Redis

from gharbasket.platform.errors import ProblemError

log = structlog.get_logger("gharbasket.rate_limit")


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
        raise ProblemError(429, code, detail, headers={"Retry-After": str(retry_after)})
