"""Per-request context: a request id on every log line and response, one access-log line, and
the request's span (M0 §6.2, platform/tracing.py), which carries the request id as `gb.request_id`.

A caller-supplied `X-Request-ID` is kept when it looks safe, so a request can be traced from the
app through the proxy to the API; otherwise a new id is generated.

Every response also gets the baseline security headers (M0 §6.4): `nosniff`, `DENY` framing and
`no-referrer` always, HSTS on staging and prod, and `Cache-Control: no-store` unless the route set
its own (the public pages send `public, max-age=300...`, platform/rate_limit.py PUBLIC_CACHE).
"""

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gharbasket.platform import tracing

REQUEST_ID_HEADER = b"x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
CACHE_CONTROL = b"cache-control"
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)
# Only where the API is reached over HTTPS (staging, prod): a browser would keep refusing plain
# http://localhost for a year after seeing it.
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")

log = structlog.get_logger("gharbasket.request")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.defaults = (*SECURITY_HEADERS, HSTS) if hsts else SECURITY_HEADERS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(REQUEST_ID_HEADER, b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        # The generic 500 is sent by Starlette's outermost middleware, past `send_with_request_id`:
        # errors._handle_unexpected adds these from here.
        scope["state"]["default_headers"] = (*self.defaults, (CACHE_CONTROL, b"no-store"))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                if REQUEST_ID_HEADER not in present:
                    headers.append((REQUEST_ID_HEADER, request_id.encode("latin-1")))
                headers.extend(item for item in self.defaults if item[0] not in present)
                if CACHE_CONTROL not in present:  # a route that chose its caching keeps it
                    headers.append((CACHE_CONTROL, b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            with tracing.request_span(scope["method"], scope["path"]) as span:
                # An unhandled error is logged by Starlette's outermost middleware, outside this
                # span: errors._handle_unexpected reads the ids from here.
                scope["state"]["trace_ids"] = tracing.log_ids(span)
                try:
                    await self.app(scope, receive, send_with_request_id)
                finally:
                    tracing.finish_request(span, scope, status_code, request_id)
                    log.info(  # inside the span, so the line carries its trace id
                        "request",
                        method=scope["method"],
                        path=scope["path"],
                        status=status_code,
                        duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    )
        finally:
            structlog.contextvars.clear_contextvars()
