"""Instant sign-out: revoked session ids kept in Valkey for as long as an access token can live.

Access tokens are checked without the database, so without this list a signed-out device's
access token would keep working for up to 15 minutes. Valkey being down must not lock everyone
out, so failures are logged and treated as "not revoked" (refresh still checks the database).
"""

import uuid

import structlog
from redis.asyncio import Redis

from gharbasket.platform.tokens import ACCESS_TOKEN_TTL, LEEWAY

TTL_S = int((ACCESS_TOKEN_TTL + LEEWAY).total_seconds())

log = structlog.get_logger("gharbasket.revocations")


def _key(session_id: uuid.UUID) -> str:
    return f"gb:revoked-session:{session_id}"


async def mark_revoked(valkey: Redis | None, session_ids: list[uuid.UUID]) -> None:
    if valkey is None or not session_ids:
        return
    try:
        async with valkey.pipeline(transaction=False) as pipe:
            for session_id in session_ids:
                pipe.set(_key(session_id), "1", ex=TTL_S)
            await pipe.execute()
    except Exception as exc:
        log.warning("revocation_not_recorded", error=type(exc).__name__)


async def is_revoked(valkey: Redis | None, session_id: uuid.UUID) -> bool:
    if valkey is None:
        return False
    try:
        return bool(await valkey.exists(_key(session_id)))
    except Exception as exc:
        log.warning("revocation_check_skipped", error=type(exc).__name__)
        return False
