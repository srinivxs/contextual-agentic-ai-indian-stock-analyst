"""Outbound HTTP for filing discovery (ADR 018): honest, bounded, and allow-listed at every hop.

`polite_get` is the only way the worker reaches the internet. It says who we are, gives up after a
timeout, follows a redirect only to another allowed address, and stops reading past a size limit.
"""

import httpx
import pytest

from app.polite_fetch import USER_AGENT, FetchRefused, FetchTooLarge, polite_get

ALLOWED = "https://allowed.example/ok"


def allow(url: str) -> bool:
    return url.startswith("https://allowed.example/")


def client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_it_says_who_we_are_and_returns_the_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"%PDF-1.7 hello")

    async with client(httpx.MockTransport(handler)) as http:
        body = await polite_get(http, ALLOWED, allowed=allow, limit=1000)

    assert body == b"%PDF-1.7 hello"
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert "github.com" in USER_AGENT  # a way to contact us, not a disguise


async def test_an_address_that_is_not_allowed_is_never_requested() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(FetchRefused):
            await polite_get(http, "https://elsewhere.example/x", allowed=allow, limit=1000)


async def test_a_redirect_is_followed_only_to_another_allowed_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok":
            return httpx.Response(302, headers={"Location": "https://allowed.example/final"})
        return httpx.Response(200, content=b"final")

    async with client(httpx.MockTransport(handler)) as http:
        assert await polite_get(http, ALLOWED, allowed=allow, limit=1000) == b"final"


async def test_a_redirect_to_anywhere_else_is_refused_before_it_is_followed() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(FetchRefused):
            await polite_get(http, ALLOWED, allowed=allow, limit=1000)
    assert requested == [ALLOWED]


async def test_an_endless_redirect_chain_is_cut_off() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": ALLOWED})

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(FetchRefused):
            await polite_get(http, ALLOWED, allowed=allow, limit=1000)


async def test_a_body_past_the_limit_is_not_read_to_the_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(FetchTooLarge):
            await polite_get(http, ALLOWED, allowed=allow, limit=1000)


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
async def test_an_error_status_is_an_error(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await polite_get(http, ALLOWED, allowed=allow, limit=1000)
