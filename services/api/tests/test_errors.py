from fastapi.testclient import TestClient

PROBLEM_JSON = "application/problem+json"


def test_problem_error_becomes_problem_json(client: TestClient) -> None:
    response = client.get("/v1/_test/problem")
    body = response.json()

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["type"] == "urn:gharbasket:problem:orders.cutoff_passed"
    assert body["code"] == "orders.cutoff_passed"
    assert body["title"] == "Conflict"
    assert body["status"] == 409
    assert body["detail"] == "Orders for this slot are already locked."
    assert body["instance"] == "/v1/_test/problem"
    assert body["request_id"] == response.headers["x-request-id"]


def test_unknown_route_is_problem_json(client: TestClient) -> None:
    response = client.get("/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "http.not_found"


def test_wrong_method_is_problem_json(client: TestClient) -> None:
    response = client.post("/v1/health")

    assert response.status_code == 405
    assert response.json()["code"] == "http.method_not_allowed"


def test_validation_error_lists_fields_without_echoing_input(client: TestClient) -> None:
    # Bodies can carry personal data (phone numbers), so submitted values must never be echoed.
    payload = {"name": "Priya", "phone": "9876543210", "age": "forty-two"}

    response = client.post("/v1/_test/profile", json=payload)
    body = response.json()

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["code"] == "request.invalid"
    assert body["errors"] == [
        {
            "loc": ["body", "age"],
            "message": "Input should be a valid integer, unable to parse string as an integer",
            "type": "int_parsing",
        }
    ]
    for value in ("9876543210", "forty-two", "Priya"):
        assert value not in response.text


def test_unexpected_error_hides_internals(client: TestClient) -> None:
    response = client.get("/v1/_test/boom")
    body = response.json()

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    assert body["code"] == "server.error"
    assert "hunter2" not in response.text
    assert body["request_id"] == response.headers["x-request-id"]


def test_request_id_header_is_not_duplicated_on_errors(client: TestClient) -> None:
    response = client.get("/v1/_test/problem")

    assert len(response.headers.get_list("x-request-id")) == 1
