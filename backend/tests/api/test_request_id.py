import asyncio
import io
import re

import httpx

from app.core.context import get_request_id
from tests.helpers import parse_log_lines


async def test_response_gets_a_generated_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/healthz")
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_each_request_gets_its_own_id(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/healthz")
    second = await client.get("/api/healthz")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_valid_inbound_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/healthz", headers={"X-Request-ID": "trace-abc-12345"})
    assert response.headers["x-request-id"] == "trace-abc-12345"


async def test_hostile_inbound_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/healthz", headers={"X-Request-ID": "not valid!!"})
    assert response.headers["x-request-id"] != "not valid!!"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_request_id_reaches_error_responses(client: httpx.AsyncClient) -> None:
    response = await client.get("/no/such/route", headers={"X-Request-ID": "trace-404-abcdef"})
    assert response.status_code == 404
    assert response.headers["x-request-id"] == "trace-404-abcdef"


async def test_handler_code_sees_the_request_id_via_contextvar(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/_test/echo-id", headers={"X-Request-ID": "ctx-async-12345"})
    assert response.json() == {"request_id": "ctx-async-12345"}


async def test_sync_endpoints_in_the_threadpool_also_see_it(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/echo-id-sync", headers={"X-Request-ID": "ctx-sync-123456"})
    assert response.json() == {"request_id": "ctx-sync-123456"}


async def test_concurrent_requests_never_see_each_others_ids(client: httpx.AsyncClient) -> None:
    ids = [f"concurrent-{n:04d}-abcd" for n in range(20)]
    responses = await asyncio.gather(
        *(client.get("/_test/echo-id", headers={"X-Request-ID": rid}) for rid in ids)
    )
    assert [r.json()["request_id"] for r in responses] == ids
    assert [r.headers["x-request-id"] for r in responses] == ids


async def test_context_is_cleared_after_the_request(client: httpx.AsyncClient) -> None:
    await client.get("/_test/ok")
    assert get_request_id() is None


async def test_the_same_id_appears_in_header_and_log_line(
    client: httpx.AsyncClient, log_stream: io.StringIO
) -> None:
    response = await client.get("/_test/ok")
    completed = [r for r in parse_log_lines(log_stream) if r["message"] == "request_completed"]
    assert len(completed) == 1
    assert completed[0]["request_id"] == response.headers["x-request-id"]
