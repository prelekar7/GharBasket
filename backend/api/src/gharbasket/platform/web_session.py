"""Browser sessions (ADR-0024): tokens in httpOnly cookies instead of JavaScript-readable storage.

The console and the browser PWA reach the API through same-origin proxies (Next.js / Vercel
rewrites, the Vite dev proxy), so the cookies are first-party and need no `Domain`:

- `__Host-gb_at`: the access token, sent on every API call (Secure, HttpOnly, SameSite=Lax, Path=/);
- `__Secure-gb_rt`: the refresh token, sent only to /v1/auth/* (Path=/v1/auth). With the prefix a
  browser accepts the cookie only from an HTTPS response that marks it `Secure`, so a plain-HTTP
  page cannot plant one (ASVS F8, 2026-11). `__Host-` would need Path=/.

A browser announces cookie mode with `X-GB-Client: web`. Cookies are sent automatically, so every
cookie-authenticated request that changes something must carry that header (another site cannot
set custom headers without a CORS preflight, which we refuse) and come from an allowed Origin.

The refresh cookie was called `gb_rt` until 2026-11. It is renamed without signing anyone out
(M1 §3.1, handbook 13 §13.3):

1. Reading: `__Secure-gb_rt` first, else `gb_rt`, so a session started before the rename keeps
   working.
2. Writing, now: every sign-in and refresh sets `__Secure-gb_rt`. A client that sent
   `__Secure-gb_rt` knows the new name, so a `gb_rt` it also sent is cleared. A client that sent no
   `__Secure-gb_rt` also gets `gb_rt` with the same value (`WRITE_LEGACY_REFRESH_COOKIE`): the
   console's server reads only `gb_rt` (frontend/console/lib/session.js) and copies cookies by
   name. A browser PWA therefore drops `gb_rt` at its first refresh.
3. Once the console reads `__Secure-gb_rt` and that is deployed, set
   `WRITE_LEGACY_REFRESH_COOKIE = False`: `gb_rt` is then never written, and cleared whenever it
   is read.
4. Remove `LEGACY_REFRESH_COOKIE` (the read and the clearing) 30 days after step 3 is deployed. A
   session lasts at most 30 days and is never extended (`SESSION_LIFETIME`), so by then no live
   session is held by a `gb_rt` cookie. Target: step 3 by 2026-11-30, step 4 on 2026-12-31. The
   log event `legacy_refresh_cookie_read` shows whether anyone still sends only `gb_rt`.
"""

import structlog
from fastapi import Request, Response

from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings

ACCESS_COOKIE = "__Host-gb_at"
REFRESH_COOKIE = "__Secure-gb_rt"
# The refresh cookie's name before 2026-11; the module docstring says when it goes.
LEGACY_REFRESH_COOKIE = "gb_rt"
WRITE_LEGACY_REFRESH_COOKIE = True
REFRESH_COOKIE_PATH = "/v1/auth"
CLIENT_HEADER = "x-gb-client"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

log = structlog.get_logger("gharbasket.web_session")


def wants_cookies(request: Request) -> bool:
    return request.headers.get(CLIENT_HEADER) == "web"


def csrf_rejected() -> ProblemError:
    return ProblemError(403, "auth.csrf_rejected", "This request did not come from our app.")


def check_web_request(request: Request, settings: Settings) -> None:
    """For cookie-authenticated or cookie-issuing requests: our header, and an allowed Origin."""
    if not wants_cookies(request):
        raise csrf_rejected()
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.allowed_web_origins:
        raise csrf_rejected()


def presented_refresh_token(request: Request) -> str | None:
    """The refresh cookie's value: the new name first, else the old one (during the rename)."""
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        return token
    legacy = request.cookies.get(LEGACY_REFRESH_COOKIE)
    if not legacy:
        return None
    log.info("legacy_refresh_cookie_read")
    return legacy


def _set_refresh_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response, name: str) -> None:
    response.delete_cookie(
        name, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="lax"
    )


def set_session_cookies(
    response: Response,
    request: Request,
    *,
    access_token: str,
    access_max_age: int,
    refresh_token: str,
    refresh_max_age: int,
) -> None:
    """Both session cookies; the old refresh cookie name is written or cleared as above."""
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=access_max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    _set_refresh_cookie(response, REFRESH_COOKIE, refresh_token, refresh_max_age)
    if REFRESH_COOKIE not in request.cookies and WRITE_LEGACY_REFRESH_COOKIE:
        _set_refresh_cookie(response, LEGACY_REFRESH_COOKIE, refresh_token, refresh_max_age)
    elif LEGACY_REFRESH_COOKIE in request.cookies:
        _clear_refresh_cookie(response, LEGACY_REFRESH_COOKIE)


def clear_session_cookies(response: Response) -> None:
    """Sign-out: the access cookie, and the refresh cookie under both names."""
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    _clear_refresh_cookie(response, REFRESH_COOKIE)
    _clear_refresh_cookie(response, LEGACY_REFRESH_COOKIE)
