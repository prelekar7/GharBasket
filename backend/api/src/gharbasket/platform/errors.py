"""Every error the API returns is RFC 9457 `application/problem+json` with a namespaced `code`.

Modules raise `ProblemError(409, "orders.cutoff_passed", "...")`; clients branch on `code`, never on
the human-readable text. Unexpected exceptions become a generic 500 without leaking internals.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"

log = structlog.get_logger("gharbasket.errors")

_HTTP_CODES = {
    404: "http.not_found",
    405: "http.method_not_allowed",
    406: "http.not_acceptable",
    415: "http.unsupported_media_type",
}


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


class ProblemError(Exception):
    """Raise to return a problem response: `ProblemError(409, "orders.cutoff_passed", "...")`.

    `detail` is shown to people; clients branch on `code`.
    """

    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        title: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title or _phrase(status)
        self.errors = errors
        self.headers = headers


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str | None = None,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:gharbasket:problem:{code}",
        "title": title or _phrase(status),
        "status": status,
        "code": code,
        "instance": request.url.path,
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors

    response_headers = dict(headers or {})
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
        response_headers.setdefault("X-Request-ID", request_id)

    return JSONResponse(
        jsonable_encoder(body),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=response_headers,
    )


async def _handle_problem(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemError)
    return problem_response(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        errors=exc.errors,
        headers=exc.headers,
    )


async def _handle_http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status = exc.status_code
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != _phrase(status) else None
    return problem_response(
        request,
        status=status,
        code=_HTTP_CODES.get(status, f"http.{status}"),
        detail=detail,
        headers=exc.headers,
    )


async def _handle_validation(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # The submitted input is deliberately not echoed back: it can contain personal data.
    errors = [
        {"loc": list(error.get("loc", ())), "message": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        code="request.invalid",
        detail="The request is not valid.",
        errors=errors,
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception(
        "unhandled_error",
        request_id=getattr(request.state, "request_id", None),
        path=request.url.path,
    )
    return problem_response(
        request,
        status=500,
        code="server.error",
        detail="Something went wrong on our side. Please try again.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _handle_problem)
    app.add_exception_handler(StarletteHTTPException, _handle_http)
    app.add_exception_handler(RequestValidationError, _handle_validation)
    app.add_exception_handler(Exception, _handle_unexpected)
