"""Instant sign-out and role revocation: Valkey lists kept as long as an access token lives.

Access tokens are checked without the database, so without these lists a signed-out device's
access token, or a token that still carries a revoked role, would keep working for up to 15
minutes:

- `gb:revoked-session:<session id>`: the session was signed out (logout, logout-all, device
  sign-out, refresh reuse, account deletion);
- `gb:revoked-grants:<user id>`: a hash of grant ("hub_manager:<hub id>", "admin") to the second it
  was revoked. A token that carries one of those grants and was issued no later than that second
  (plus `CLOCK_SKEW_S`) is refused, so the app refreshes and gets the roles from the database. A
  token issued after the revocation does not carry the grant, unless it was given back: the
  admin endpoint then removes the grant from the hash (`clear_grant_revoked`), and a grant given
  back any other way (the operator command) is in a token issued a later second, so it passes.

Only tokens with a role besides `customer` read the second key; it travels in the same round trip
as the first. Valkey being down must not lock everyone out, so failures are logged and treated as
"not revoked" (a refresh still reads the session and the roles from the database).
"""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis

from gharbasket.platform.tokens import ACCESS_TOKEN_TTL, LEEWAY, AccessClaims

TTL_S = int((ACCESS_TOKEN_TTL + LEEWAY).total_seconds())
# Tokens are issued by every API instance; this covers a small clock difference between two of
# them. The price: a role given back by the operator command within these seconds of its
# revocation waits for the key to expire (TTL_S) before a token carrying it works again.
CLOCK_SKEW_S = 5
SIGNED_OUT = "You have signed out on this device."
ROLES_CHANGED = "Your roles have changed. Refresh the session."

log = structlog.get_logger("gharbasket.revocations")


def _key(session_id: uuid.UUID) -> str:
    return f"gb:revoked-session:{session_id}"


def _grants_key(user_id: uuid.UUID) -> str:
    return f"gb:revoked-grants:{user_id}"


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


async def mark_grants_revoked(
    valkey: Redis | None,
    user_id: uuid.UUID,
    grants: Collection[str],
    *,
    now: datetime | None = None,
) -> None:
    """Call after the transaction that removed the grants has committed: a token issued before
    the commit may carry them, one issued after cannot."""
    if valkey is None or not grants:
        return
    second = int((now or datetime.now(UTC)).timestamp()) + CLOCK_SKEW_S
    key = _grants_key(user_id)
    try:
        async with valkey.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping={grant: second for grant in grants})
            pipe.expire(key, TTL_S + CLOCK_SKEW_S)
            await pipe.execute()
    except Exception as exc:
        log.warning("grant_revocation_not_recorded", error=type(exc).__name__)


async def clear_grant_revoked(valkey: Redis | None, user_id: uuid.UUID, grant: str) -> None:
    """Call after a grant given back has committed, so a token carrying it works at once (the
    tokens from before the revocation then work again too: the role is theirs again)."""
    if valkey is None:
        return
    try:
        await valkey.hdel(_grants_key(user_id), grant)
    except Exception as exc:
        log.warning("grant_revocation_not_cleared", error=type(exc).__name__)


async def refusal(valkey: Redis | None, claims: AccessClaims) -> str | None:
    """Why this verified access token must not be used now, or None."""
    if valkey is None:
        return None
    elevated = [role for role in claims.roles if role != "customer"]
    revoked_at: list[bytes | str | None] = []
    try:
        if not elevated:
            signed_out = await valkey.exists(_key(claims.session_id))
        else:
            async with valkey.pipeline(transaction=False) as pipe:
                pipe.exists(_key(claims.session_id))
                pipe.hmget(_grants_key(claims.user_id), elevated)
                signed_out, revoked_at = await pipe.execute()
    except Exception as exc:
        log.warning("revocation_check_skipped", error=type(exc).__name__)
        return None
    if signed_out:
        return SIGNED_OUT
    issued = int(claims.issued_at.timestamp())
    if any(at is not None and issued <= int(at) for at in revoked_at):
        return ROLES_CHANGED
    return None
