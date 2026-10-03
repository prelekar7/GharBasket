"""Who is calling: the access-token check every protected endpoint uses.

    @router.get("/me")
    async def me(principal: CurrentPrincipal, session: DbSession) -> MeOut: ...

Apps send `Authorization: Bearer <access token>`; browsers instead carry it in the httpOnly
`__Host-gb_at` cookie (platform/web_session.py). A Bearer header always wins over a cookie.
The token is checked without the database (signature, expiry, audience); a signed-out session is
refused at once through the Valkey list in platform/revocations.py.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gharbasket.platform.revocations import is_revoked
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.tokens import AccessClaims, invalid_token, verify_access_token
from gharbasket.platform.web_session import ACCESS_COOKIE, SAFE_METHODS, check_web_request

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
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(ACCESS_COOKIE)
        if token is None:
            raise invalid_token("Sign in first.")
        if request.method not in SAFE_METHODS:
            check_web_request(request, settings)
    claims = verify_access_token(settings, token)
    if await is_revoked(getattr(request.app.state, "valkey", None), claims.session_id):
        raise invalid_token("You have signed out on this device.")
    # Idempotency keys and logs are scoped to the caller from here on.
    request.state.principal_id = str(claims.user_id)
    return claims


CurrentPrincipal = Annotated[AccessClaims, Depends(require_access_token)]
