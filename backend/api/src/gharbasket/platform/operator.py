"""Operator-only endpoints (`/v1/ready`, later `/v1/internal/jobs/*`), protected by JOBS_TOKEN.

These are called by people and by GitHub Actions, never by the apps, so they use one shared secret
instead of a user login. Without the token anyone could poll /v1/ready, which queries the database,
and keep Neon's free compute awake. Locally (no JOBS_TOKEN) they stay open.
"""

import hmac
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings, get_settings

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="OperatorToken",
    description="JOBS_TOKEN from Infisical. Only for operators and scheduled jobs.",
)


async def require_operator(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected = settings.jobs_token
    if expected is None:
        return
    given = credentials.credentials if credentials else ""
    if not hmac.compare_digest(given.encode(), expected.get_secret_value().encode()):
        raise ProblemError(
            401,
            "platform.operator_token_required",
            "This endpoint needs the operator token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
