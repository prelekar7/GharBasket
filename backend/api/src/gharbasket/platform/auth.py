"""Who is calling: the `Authorization: Bearer <access token>` check every protected endpoint uses.

    @router.get("/me")
    async def me(principal: CurrentPrincipal, session: DbSession) -> MeOut: ...

The token is checked without a database round trip (signature, expiry, audience). Logging out
makes the access token stop working within its 15 minutes at most; an immediate deny-list in
Valkey arrives with the web-cookie PR.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.tokens import AccessClaims, invalid_token, verify_access_token

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AccessToken",
    description="Access token from POST /v1/auth/exchange or /v1/auth/refresh (15 minutes).",
)


async def require_access_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AccessClaims:
    if credentials is None:
        raise invalid_token("Sign in first.")
    claims = verify_access_token(settings, credentials.credentials)
    # Idempotency keys and logs are scoped to the caller from here on.
    request.state.principal_id = str(claims.user_id)
    return claims


CurrentPrincipal = Annotated[AccessClaims, Depends(require_access_token)]
