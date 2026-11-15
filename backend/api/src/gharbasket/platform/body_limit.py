"""A cap on request bodies (M0 §6.4): 413 `platform.payload_too_large` past the limit.

Uploads go straight to object storage (M0 §3.1), so no legitimate body is large: sign-in, webhooks
and every app request fit well inside `MAX_BODY_BYTES`. The operator's internal endpoints take up
to 5,000 items from the training jobs (about 2.7 MB of feature snapshots, 1.3 MB of forecasts), so
`/v1/internal/` has its own, larger cap.

A declared `Content-Length` over the cap is refused before anything reads the body. A chunked body
(or one whose length was not declared) is counted while it streams: the read that passes the cap
raises `PayloadTooLarge`, which FastAPI passes through as an HTTP error (it turns other errors
raised while reading the body into a 400), so the route answers 413 and never sees the rest.
"""

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gharbasket.platform.errors import problem_response

MiB = 1024 * 1024
MAX_BODY_BYTES = 1 * MiB
INTERNAL_PREFIX = "/v1/internal/"
MAX_INTERNAL_BODY_BYTES = 8 * MiB

CODE = "platform.payload_too_large"
DETAIL = "The request body is too large."


class PayloadTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, DETAIL)


def limit_for(path: str) -> int:
    return MAX_INTERNAL_BODY_BYTES if path.startswith(INTERNAL_PREFIX) else MAX_BODY_BYTES


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or []:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # the server refuses a malformed length before it reaches us
    return None


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope["path"])
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _refuse(scope, receive, send)
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise PayloadTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except PayloadTooLarge:
            # Raised outside a route's error handlers (a middleware reading the body); the
            # response can still be sent unless one has started.
            if started:
                raise
            await _refuse(scope, receive, send)


async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
    request = Request(scope, receive)
    response = problem_response(request, status=413, code=CODE, detail=DETAIL)
    # The rest of the body is never read: the connection is not reused for another request.
    response.headers["Connection"] = "close"
    await response(scope, receive, send)
