"""Access tokens: short-lived JWTs the apps send as `Authorization: Bearer <token>` (ADR-0024).

- HS256, signed with JWT_SIGNING_KEY; the `kid` header names the key, so keys can be rotated.
- 15 minutes; checked with 30 seconds of leeway for clock differences between phones and servers.
- Claims: iss, aud, sub (user id), sid (session id), roles, iat, exp, jti.

Refresh tokens are not JWTs: they are random strings stored hashed by the identity module.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings

ACCESS_TOKEN_TTL = timedelta(minutes=15)
LEEWAY = timedelta(seconds=30)
ALGORITHM = "HS256"

# Refuses keys shorter than the hash output (RFC 7518 §3.2) instead of only warning.
_jwt = jwt.PyJWT(options={"enforce_minimum_key_length": True})


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    # "customer", or "<role>:<scope_id>" for scoped roles such as "captain:<society id>".
    roles: tuple[str, ...]
    token_id: str
    expires_at: datetime


def invalid_token(detail: str = "Sign in again.") -> ProblemError:
    return ProblemError(401, "auth.invalid_token", detail, headers={"WWW-Authenticate": "Bearer"})


def issue_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    roles: tuple[str, ...],
    now: datetime | None = None,
) -> tuple[str, int]:
    """Return the token and its lifetime in seconds (`access_expires_in` in the API)."""
    issued = now or datetime.now(UTC)
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "sid": str(session_id),
        "roles": list(roles),
        "iat": issued,
        "exp": issued + ACCESS_TOKEN_TTL,
        "jti": uuid.uuid4().hex,
    }
    token = _jwt.encode(
        claims, settings.jwt_active_key, algorithm=ALGORITHM, headers={"kid": settings.jwt_key_id}
    )
    return token, int(ACCESS_TOKEN_TTL.total_seconds())


def verify_access_token(settings: Settings, token: str) -> AccessClaims:
    """Check signature, key id, issuer, audience and expiry; any problem is a 401."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = settings.jwt_verification_keys.get(kid) if isinstance(kid, str) else None
        if key is None:
            raise invalid_token()
        claims = _jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=LEEWAY,
            options={"require": ["iss", "aud", "sub", "sid", "iat", "exp", "jti"]},
        )
        roles = claims.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):  # pyright: ignore[reportUnknownVariableType]
            raise invalid_token()
        return AccessClaims(
            user_id=uuid.UUID(claims["sub"]),
            session_id=uuid.UUID(claims["sid"]),
            roles=tuple(roles),  # pyright: ignore[reportUnknownArgumentType]
            token_id=str(claims["jti"]),
            expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise invalid_token("The session has expired; refresh it.") from exc
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise invalid_token() from exc
