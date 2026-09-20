"""Auth endpoints that must answer WITHOUT touching the database.

A request with no session cookie has nothing to look up, so it must never cost a connection. The
session factory is replaced by one that fails the test (as a 500) if anything asks it for a session.
"""

from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI

from tests.helpers import parse_set_cookie


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


@pytest.fixture(autouse=True)
def forbid_database(app: FastAPI) -> None:
    app.state.session_factory = NoDatabase()


def assert_401(response: httpx.Response) -> None:
    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"] == "Not authenticated"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


async def test_me_without_a_cookie_is_a_401_envelope(client: httpx.AsyncClient) -> None:
    assert_401(await client.get("/api/v1/me"))


async def test_me_with_an_empty_session_cookie_is_treated_as_no_cookie(
    client: httpx.AsyncClient,
) -> None:
    assert_401(await client.get("/api/v1/me", headers={"Cookie": "session="}))


async def test_the_production_cookie_name_is_ignored_in_development(
    client: httpx.AsyncClient,
) -> None:
    """Only the name this environment issues is ever read."""
    assert_401(await client.get("/api/v1/me", headers={"Cookie": "__Host-session=anything"}))


async def test_logout_without_a_cookie_succeeds_and_still_clears_it(
    client: httpx.AsyncClient,
) -> None:
    """Logout is idempotent: 'you are logged out' is true whether or not you were logged in."""
    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    name, _, attributes = parse_set_cookie(response.headers["set-cookie"])
    assert name == "session"
    assert attributes["max-age"] == "0"


async def test_logout_must_be_a_post(client: httpx.AsyncClient) -> None:
    """A link, an image or a prefetch (all GET) must not be able to log someone out."""
    response = await client.get("/api/v1/auth/logout")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost:9999",  # same host, different port is a different origin
        "https://localhost:8000",  # same host and port, different scheme
        "http://localhost:8000.evil.example",
        "null",  # sandboxed frames and data: URLs send this
    ],
)
async def test_logout_from_a_foreign_origin_is_refused_before_anything_else_happens(
    client: httpx.AsyncClient, origin: str
) -> None:
    response = await client.post("/api/v1/auth/logout", headers={"Origin": origin})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers  # a forged request must not touch the session


@pytest.mark.parametrize(
    "origin", ["http://localhost:8000", None], ids=["same-origin", "no-origin"]
)
async def test_logout_from_our_own_origin_or_a_non_browser_client_is_allowed(
    client: httpx.AsyncClient, origin: str | None
) -> None:
    headers = {"Origin": origin} if origin else {}
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204


async def test_reading_me_is_not_blocked_by_the_origin_rule(client: httpx.AsyncClient) -> None:
    """The rule guards state-changing methods. Reads are protected by cookies and CORS instead."""
    response = await client.get("/api/v1/me", headers={"Origin": "https://evil.example"})
    assert response.status_code == 401  # unauthenticated, but not 403
