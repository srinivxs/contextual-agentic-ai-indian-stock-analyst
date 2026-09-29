"""The stocks and follow endpoints must refuse an anonymous or forged request WITHOUT the database.

A request with no session cookie has nothing to look up, and a foreign `Origin` is decided from the
request alone, so neither may cost a connection. The session factory is replaced by one that fails
the test if anything asks it for a session.
"""

from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI

FOLLOW = "/api/v1/stocks/TCS/follow"


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


@pytest.fixture(autouse=True)
def forbid_database(app: FastAPI) -> None:
    app.state.session_factory = NoDatabase()


def assert_401(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


async def test_listing_the_stocks_needs_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.get("/api/v1/stocks"))


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_following_and_unfollowing_need_a_session(
    client: httpx.AsyncClient, method: str
) -> None:
    assert_401(await client.request(method, FOLLOW))


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "http://localhost:9999", "http://localhost:8000.evil.example", "null"],
)
async def test_a_foreign_origin_is_refused_before_anything_else(
    client: httpx.AsyncClient, method: str, origin: str
) -> None:
    """The Origin check runs first, so a cross-site page cannot learn whether you are signed in."""
    response = await client.request(method, FOLLOW, headers={"Origin": origin})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_our_own_origin_or_no_origin_gets_as_far_as_the_session_check(
    client: httpx.AsyncClient, method: str
) -> None:
    for headers in ({"Origin": "http://localhost:8000"}, {}):
        assert_401(await client.request(method, FOLLOW, headers=headers))


async def test_reading_the_list_is_not_blocked_by_the_origin_rule(
    client: httpx.AsyncClient,
) -> None:
    """The rule guards state-changing methods; reads are protected by the cookie alone."""
    response = await client.get("/api/v1/stocks", headers={"Origin": "https://evil.example"})
    assert response.status_code == 401  # not 403


@pytest.mark.parametrize("symbol", ["tcs", "T" * 21, "TCS;DROP", "T%20S"])
async def test_a_badly_shaped_symbol_gets_401_before_it_is_looked_at(
    client: httpx.AsyncClient, symbol: str
) -> None:
    """Authentication comes before validation, so nothing is revealed to an anonymous caller."""
    assert_401(await client.put(f"/api/v1/stocks/{symbol}/follow"))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", FOLLOW),
        ("PATCH", FOLLOW),
        ("GET", FOLLOW),
        ("POST", "/api/v1/stocks"),
        ("PUT", "/api/v1/stocks"),
        ("DELETE", "/api/v1/stocks"),
    ],
)
async def test_only_the_intended_methods_exist(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    """A link or prefetch (GET) must never be able to follow anything."""
    response = await client.request(method, path)

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("method", "url"), [("GET", "/api/v1/data/status"), ("POST", "/api/v1/data/refresh")]
)
async def test_the_data_status_and_update_need_a_session(
    client: httpx.AsyncClient, method: str, url: str
) -> None:
    assert_401(await client.request(method, url))


async def test_a_foreign_origin_cannot_start_an_update(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/data/refresh", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_the_insights_need_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.get("/api/v1/stocks/TCS/insights"))
