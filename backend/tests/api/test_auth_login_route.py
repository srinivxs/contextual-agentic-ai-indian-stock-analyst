"""`GET /api/v1/auth/google/login`: start a login and send the browser to Google.

Nothing here touches the database or the network. It mints one login attempt, remembers it in a
signed cookie, and redirects.
"""

from typing import NoReturn
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI

from app.auth.google import redirect_uri
from app.auth.login_state import read_login_attempt
from app.auth.pkce import code_challenge
from app.main import create_app
from tests.helpers import build_settings, parse_set_cookie, production_settings

LOGIN = "/api/v1/auth/google/login"
COOKIE = "oauth_login"


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("starting a login must not touch the database")


@pytest.fixture(autouse=True)
def forbid_database(app: FastAPI) -> None:
    app.state.session_factory = NoDatabase()


def login_cookie(response: httpx.Response) -> tuple[str, dict[str, str]]:
    for header in response.headers.get_list("set-cookie"):
        name, value, attributes = parse_set_cookie(header)
        if name == COOKIE:
            return value.strip('"'), attributes
    raise AssertionError("no oauth_login cookie was set")


async def test_it_redirects_the_browser_to_google(client: httpx.AsyncClient) -> None:
    response = await client.get(LOGIN)

    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert response.headers["cache-control"] == "no-store"


async def test_the_redirect_carries_the_attempt_we_just_remembered(
    client: httpx.AsyncClient,
) -> None:
    """The cookie and the URL must describe the same login, or the callback can never match them."""
    response = await client.get(LOGIN)

    signed, _ = login_cookie(response)
    attempt = read_login_attempt(signed, build_settings())
    query = {k: v[0] for k, v in parse_qs(urlparse(response.headers["location"]).query).items()}
    assert query["state"] == attempt.state
    assert query["nonce"] == attempt.nonce
    assert query["code_challenge"] == code_challenge(attempt.code_verifier)
    assert query["redirect_uri"] == redirect_uri(build_settings())


async def test_the_verifier_stays_in_the_cookie_and_never_reaches_the_url(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(LOGIN)
    signed, _ = login_cookie(response)
    attempt = read_login_attempt(signed, build_settings())
    assert attempt.code_verifier not in response.headers["location"]


async def test_the_login_cookie_is_httponly_lax_and_short_lived(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(LOGIN)
    _, attributes = login_cookie(response)

    assert "httponly" in attributes  # JavaScript must never read it
    # Lax, NOT strict: the callback is a top-level navigation from google.com, and a Strict cookie
    # would not be sent on it, breaking every login.
    assert attributes["samesite"].lower() == "lax"
    assert attributes["max-age"] == str(build_settings().oauth_login_ttl_seconds)
    assert "secure" not in attributes  # local plain HTTP


async def test_the_login_cookie_is_scoped_to_the_auth_endpoints(
    client: httpx.AsyncClient,
) -> None:
    """It is only needed by the callback, so it is not sent with every other request."""
    _, attributes = login_cookie(await client.get(LOGIN))
    assert attributes["path"] == "/api/v1/auth/google"


async def test_in_production_the_login_cookie_is_secure() -> None:
    """It keeps its plain name: `__Host-` demands Path=/, and this cookie is deliberately scoped."""
    app = create_app(production_settings())
    app.state.session_factory = NoDatabase()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _, attributes = login_cookie(await client.get(LOGIN))

    assert "secure" in attributes
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "lax"


async def test_starting_a_login_twice_produces_two_independent_attempts(
    client: httpx.AsyncClient,
) -> None:
    settings = build_settings()
    attempts = []
    for _ in range(5):
        signed, _ = login_cookie(await client.get(LOGIN))
        attempts.append(read_login_attempt(signed, settings))

    assert len({a.state for a in attempts}) == 5
    assert len({a.nonce for a in attempts}) == 5
    assert len({a.code_verifier for a in attempts}) == 5


async def test_the_login_url_never_carries_our_client_secret(client: httpx.AsyncClient) -> None:
    response = await client.get(LOGIN)
    location = response.headers["location"]
    assert "client_secret" not in location
    assert build_settings().google_client_secret.get_secret_value() not in location


async def test_a_login_started_while_already_signed_in_is_still_a_fresh_attempt(
    client: httpx.AsyncClient,
) -> None:
    """Re-authenticating must not reuse anything from a previous attempt."""
    first, _ = login_cookie(await client.get(LOGIN))
    second, _ = login_cookie(await client.get(LOGIN, headers={"Cookie": f"{COOKIE}={first}"}))
    assert first != second


async def test_only_get_starts_a_login(client: httpx.AsyncClient) -> None:
    response = await client.post(LOGIN)
    assert response.status_code == 405
