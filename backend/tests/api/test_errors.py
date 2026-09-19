import io

import httpx

from tests.helpers import parse_log_lines


def assert_envelope(response: httpx.Response, *, code: str) -> dict[str, object]:
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["x-request-id"]
    return error  # type: ignore[no-any-return]


async def test_unknown_route_is_a_404_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/nope")
    assert response.status_code == 404
    assert_envelope(response, code="not_found")


async def test_app_error_maps_to_its_status_code_and_message(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/conflict")
    assert response.status_code == 409
    error = assert_envelope(response, code="conflict")
    assert error["message"] == "Already exists"


async def test_validation_error_is_a_422_envelope_with_safe_details(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/_test/items/not-a-number")
    assert response.status_code == 422
    error = assert_envelope(response, code="validation_error")
    details = error["details"]
    assert isinstance(details, list)
    assert details[0]["loc"] == ["path", "item_id"]
    # We never echo the caller's raw input back in an error body.
    assert all("input" not in d for d in details)
    assert "not-a-number" not in response.text


async def test_valid_input_passes_validation(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/items/42")
    assert response.status_code == 200
    assert response.json() == {"item_id": 42}


async def test_unhandled_exception_is_a_generic_500_that_leaks_nothing(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/_test/crash", headers={"X-Request-ID": "trace-500-abcdef"})
    assert response.status_code == 500
    error = assert_envelope(response, code="internal_error")
    assert error["message"] == "Internal server error"
    assert "boom-secret-internal-detail" not in response.text
    # Regression guard: this response is produced by Starlette's outermost middleware, outside
    # ours, so it needs the ID from request.state and must set the header itself.
    assert response.headers["x-request-id"] == "trace-500-abcdef"


async def test_unhandled_exception_is_logged_with_traceback_and_request_id(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    await client.get("/_test/crash", headers={"X-Request-ID": "trace-log-abcdef"})
    errors = [r for r in parse_log_lines(log_stream) if r["message"] == "unhandled_exception"]
    assert len(errors) == 1
    assert errors[0]["level"] == "ERROR"
    assert errors[0]["request_id"] == "trace-log-abcdef"
    assert "RuntimeError: boom-secret-internal-detail" in errors[0]["exception"]
