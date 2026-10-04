"""The OpenAPI contract the frontend client is generated from (ADR-0022).

Two rules are applied to the spec FastAPI builds, so every endpoint documents them the same way:
- every error (4xx, 5xx) is RFC 9457 `application/problem+json` with one `Problem` schema,
  instead of FastAPI's default `application/json` validation-error shape;
- answers that come from the shared dependencies are documented wherever those are used: 401
  (access token), 403 (`X-Active-Role` / permissions), 400 (`Idempotency-Key`, unreadable body),
  428 (`If-Match`).
Endpoint-specific descriptions written in `responses=` are kept.
"""

from typing import Any

from fastapi import FastAPI

PROBLEM_SCHEMA: dict[str, Any] = {
    "title": "Problem",
    "description": "RFC 9457 problem details. Clients branch on `code`, never on the text.",
    "type": "object",
    "required": ["type", "title", "status", "code"],
    "properties": {
        "type": {"type": "string", "examples": ["urn:gharbasket:problem:orders.cutoff_passed"]},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "code": {"type": "string", "examples": ["orders.cutoff_passed"]},
        "detail": {"type": "string"},
        "instance": {"type": "string"},
        "request_id": {"type": "string"},
        "errors": {
            "description": "Field errors of `request.invalid` (422)",
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "loc": {"type": "array", "items": {"type": ["string", "integer"]}},
                    "message": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
        },
    },
    # Extension members, e.g. `household_id` on identity.household_exists.
    "additionalProperties": True,
}
_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}

# Header parameter (lower-case) → the answer its platform dependency can give.
_HEADER_ERRORS = {
    "x-active-role": ("403", "auth.forbidden or auth.role_not_granted"),
    "idempotency-key": (
        "400",
        "request.idempotency_key_required or request.idempotency_key_invalid",
    ),
    "if-match": ("428", "request.precondition_required: send If-Match"),
}


def _document_errors(spec: dict[str, Any]) -> None:
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    schemas["Problem"] = PROBLEM_SCHEMA
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            responses: dict[str, Any] = operation.setdefault("responses", {})
            if operation.get("security"):
                responses.setdefault("401", {"description": "auth.invalid_token"})
            if "requestBody" in operation:
                # Bytes that are not even text (FastAPI answers before validation runs).
                responses.setdefault("400", {"description": "http.400: the body cannot be read"})
            for parameter in operation.get("parameters", []):
                if parameter.get("in") == "header" and parameter["name"].lower() in _HEADER_ERRORS:
                    status, description = _HEADER_ERRORS[parameter["name"].lower()]
                    responses.setdefault(status, {"description": description})
            for status, response in responses.items():
                if status[0] in "45":
                    if status == "422" and response.get("description") == "Validation Error":
                        response["description"] = "request.invalid"
                    response["content"] = _PROBLEM_CONTENT


def install(app: FastAPI) -> None:
    """Apply the rules above whenever the app builds its OpenAPI spec."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            _document_errors(build())
        assert app.openapi_schema is not None
        return app.openapi_schema

    app.openapi = openapi
