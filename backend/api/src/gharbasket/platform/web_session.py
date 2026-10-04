"""Browser sessions (ADR-0024): tokens in httpOnly cookies instead of JavaScript-readable storage.

The console and the browser PWA reach the API through same-origin proxies (Next.js / Vercel
rewrites, the Vite dev proxy), so the cookies are first-party and need no `Domain`:

- `__Host-gb_at`: the access token, sent on every API call (Secure, HttpOnly, SameSite=Lax, Path=/);
- `gb_rt`: the refresh token, sent only to /v1/auth/* (Path=/v1/auth).

A browser announces cookie mode with `X-GB-Client: web`. Cookies are sent automatically, so every
cookie-authenticated request that changes something must carry that header (another site cannot
set custom headers without a CORS preflight, which we refuse) and come from an allowed Origin.
"""

from fastapi import Request, Response

from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings

ACCESS_COOKIE = "__Host-gb_at"
REFRESH_COOKIE = "gb_rt"
REFRESH_COOKIE_PATH = "/v1/auth"
CLIENT_HEADER = "x-gb-client"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


def set_session_cookies(
    response: Response,
    *,
    access_token: str,
    access_max_age: int,
    refresh_token: str,
    refresh_max_age: int,
) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=access_max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=refresh_max_age,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="lax"
    )
