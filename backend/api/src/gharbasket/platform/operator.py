"""Operator-only endpoints (`/v1/ready`, later `/v1/internal/jobs/*`), protected by JOBS_TOKEN.

These are called by people and by GitHub Actions, never by the apps, so they use one shared secret
instead of a user login. Without the token anyone could poll /v1/ready, which queries the database,
and keep Neon's free compute awake. Locally (no JOBS_TOKEN) they stay open.

A wrong or missing token is counted per client address: past `FAILED_PER_IP` the caller gets 429
`platform.operator_rate_limited` instead of 401. A right token is never counted or refused, so a
noisy neighbour on a shared address (a GitHub runner, a proxy) cannot stop the scheduled jobs. The
token's length (at least 32 bytes on staging and prod, platform/settings.py) is what makes guessing
hopeless; the limit keeps a guessing flood short and visible.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gharbasket.platform import rate_limit
from gharbasket.platform.client_ip import limit_key
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.valkey import Valkey

FAILED_PER_IP = (20, 600)  # wrong or missing tokens per address: 20 per 10 minutes

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="OperatorToken",
    description="JOBS_TOKEN from Infisical. Only for operators and scheduled jobs.",
)


async def require_operator(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    valkey: Valkey,
) -> None:
    expected = settings.jobs_token
    if expected is None:
        return
    given = credentials.credentials if credentials else ""
    if not hmac.compare_digest(given.encode(), expected.get_secret_value().encode()):
        limit, window_s = FAILED_PER_IP
        await rate_limit.enforce(
            valkey,
            f"operator:ip:{limit_key(request)}",
            limit=limit,
            window_s=window_s,
            code="platform.operator_rate_limited",
            detail="Too many requests without the operator token. Wait a little and try again.",
        )
        raise ProblemError(
            401,
            "platform.operator_token_required",
            "This endpoint needs the operator token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
