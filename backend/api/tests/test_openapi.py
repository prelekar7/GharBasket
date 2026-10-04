"""The OpenAPI contract the frontend client is generated from (platform/openapi.py, ADR-0022)."""

from typing import Any

import pytest

from gharbasket.tools.export_openapi import build_spec

SPEC = build_spec()
PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, operation)
        for path, item in SPEC["paths"].items()
        for method, operation in item.items()
    ]


def test_every_error_is_problem_json_with_one_schema() -> None:
    assert "HTTPValidationError" not in SPEC["components"]["schemas"]
    assert SPEC["components"]["schemas"]["Problem"]["required"] == [
        "type",
        "title",
        "status",
        "code",
    ]
    for method, path, operation in _operations():
        for status, response in operation["responses"].items():
            if status[0] in "45":
                assert response["content"] == PROBLEM, f"{method} {path} {status}"


@pytest.mark.parametrize(
    ("method", "path", "statuses"),
    [
        ("POST", "/v1/households", {"400", "401", "403", "422"}),  # Idempotency-Key, role, body
        ("PATCH", "/v1/households/{household_id}", {"401", "403", "412", "428"}),  # If-Match
        ("GET", "/v1/me", {"401"}),
        ("GET", "/v1/health", set()),
    ],
)
def test_platform_answers_are_documented_where_they_can_happen(
    method: str, path: str, statuses: set[str]
) -> None:
    responses = SPEC["paths"][path][method.lower()]["responses"]

    assert statuses <= set(responses)
    if not statuses:
        assert not {status for status in responses if status[0] == "4"}


def test_endpoint_descriptions_are_kept() -> None:
    responses = SPEC["paths"]["/v1/households"]["post"]["responses"]

    assert "identity.household_exists" in responses["409"]["description"]
