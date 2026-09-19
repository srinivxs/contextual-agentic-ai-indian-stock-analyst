import io
import logging

import httpx

from app.core.logging import JsonFormatter
from app.main import create_app
from tests.helpers import build_settings, parse_log_lines


def completed(stream: io.StringIO) -> list[dict[str, object]]:
    return [r for r in parse_log_lines(stream) if r["message"] == "request_completed"]


async def test_one_structured_line_per_request(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    await client.get("/_test/ok")
    (line,) = completed(log_stream)
    assert line["logger"] == "app.request"
    assert line["level"] == "INFO"
    assert line["method"] == "GET"
    assert line["path"] == "/_test/ok"
    assert line["status_code"] == 200
    assert isinstance(line["duration_ms"], float)
    assert line["duration_ms"] >= 0


async def test_query_strings_are_never_logged(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    """Later, OAuth callbacks carry secrets (code, state) in the query string."""
    await client.get("/_test/ok?code=super-secret-oauth-code")
    # Only look at what the *app* logged: the httpx test client logs its own request URL.
    app_lines = [r for r in parse_log_lines(log_stream) if str(r["logger"]).startswith("app.")]
    assert app_lines, "expected the app to log the request"
    assert "super-secret-oauth-code" not in str(app_lines)
    assert completed(log_stream)[0]["path"] == "/_test/ok"


async def test_error_responses_are_logged_with_their_status(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    await client.get("/_test/conflict")
    assert completed(log_stream)[0]["status_code"] == 409


async def test_unhandled_errors_are_logged_as_500(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    await client.get("/_test/crash")
    assert completed(log_stream)[0]["status_code"] == 500


async def test_health_checks_are_quiet_at_info(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    """The load balancer polls /api/healthz constantly; that must not flood the logs."""
    await client.get("/api/healthz")
    assert completed(log_stream) == []


async def test_health_checks_are_logged_at_debug() -> None:
    app = create_app(build_settings(log_level="DEBUG"))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            await http.get("/api/healthz")
    finally:
        logging.getLogger().removeHandler(handler)
    (line,) = completed(stream)
    assert line["level"] == "DEBUG"
    assert line["path"] == "/api/healthz"
