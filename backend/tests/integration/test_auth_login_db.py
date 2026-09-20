"""A whole Google login, end to end, against the real database.

The only fake is Google itself. Everything else is real: the routes, the signed cookie, the token
exchange, signature verification, the user upsert and the session row.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.jwks import JwksCache
from app.auth.login_state import LoginAttempt, read_login_attempt
from app.auth.sessions import hash_session_token
from app.main import create_app
from tests.google_fakes import EMAIL, SUB, WRONG_KEY, FakeGoogle, mint_id_token
from tests.helpers import parse_set_cookie
from tests.integration.auth_helpers import count_sessions, open_session
from tests.integration.conftest import DbConfig, MakeUser

LOGIN = "/api/v1/auth/google/login"
CALLBACK = "/api/v1/auth/google/callback"
LOGIN_COOKIE = "oauth_login"
CODE = "4/0Afake-authorization-code"

pytestmark = pytest.mark.usefixtures("clean_auth_tables")


@asynccontextmanager
async def logging_in(
    db_config: DbConfig, google: FakeGoogle
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """A real app whose only fake is Google."""
    settings = db_config.settings()
    app = create_app(settings)
    app.state.http_client = google.client()
    app.state.jwks = JwksCache(client=google.client())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield app, client


def cookie_from(response: httpx.Response, name: str) -> str | None:
    for header in response.headers.get_list("set-cookie"):
        found, value, _ = parse_set_cookie(header)
        if found == name:
            return value.strip('"')
    return None


async def start_login(client: httpx.AsyncClient, settings: Any) -> tuple[str, LoginAttempt]:
    """Hit the login route and read back the attempt the server just remembered."""
    response = await client.get(LOGIN)
    signed = cookie_from(response, LOGIN_COOKIE)
    assert signed is not None
    return signed, read_login_attempt(signed, settings)


async def finish_login(
    client: httpx.AsyncClient, signed: str, attempt: LoginAttempt, **params: str
) -> httpx.Response:
    return await client.get(
        CALLBACK,
        params={"code": CODE, "state": attempt.state, **params},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


# --- the happy path -----------------------------------------------------------------------------


async def test_a_first_login_creates_the_user_and_a_session(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        response = await finish_login(client, signed, attempt)

        assert response.status_code in (302, 307)
        assert response.headers["location"] == "http://localhost:8000/"
        session_token = cookie_from(response, "session")
        assert session_token is not None

        me = await client.get("/api/v1/me", headers={"Cookie": f"session={session_token}"})

    assert me.status_code == 200
    assert me.json()["email"] == EMAIL

    async with admin_engine.connect() as connection:
        user = (await connection.execute(text("SELECT google_sub, email FROM users"))).one()
        stored = (await connection.execute(text("SELECT token_hash FROM sessions"))).scalar_one()
    assert (user.google_sub, user.email) == (SUB, EMAIL)
    assert bytes(stored) == hash_session_token(session_token)  # the hash, never the token


async def test_the_exchange_carried_the_verifier_from_our_own_cookie(
    db_config: DbConfig, google: FakeGoogle
) -> None:
    """The verifier never left the server: it went from the signed cookie straight to Google."""
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        await finish_login(client, signed, attempt)

    assert google.token_calls[0]["code_verifier"] == attempt.code_verifier
    assert google.token_calls[0]["code"] == CODE
    assert google.token_calls[0]["grant_type"] == "authorization_code"


async def test_the_login_cookie_is_cleared_once_it_has_been_used(
    db_config: DbConfig, google: FakeGoogle
) -> None:
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        response = await finish_login(client, signed, attempt)

    cleared = [
        parse_set_cookie(h)
        for h in response.headers.get_list("set-cookie")
        if parse_set_cookie(h)[0] == LOGIN_COOKIE
    ]
    assert cleared, "the one-shot login cookie must be cleared"
    assert cleared[0][2]["max-age"] == "0"


async def test_the_same_login_cookie_cannot_be_used_twice(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    """Replaying a whole callback must not mint a second session from one consent."""
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        await finish_login(client, signed, attempt)
        google.token_endpoint_fails(400)  # Google refuses a reused authorization code
        second = await finish_login(client, signed, attempt)

    assert parse_qs(urlparse(second.headers["location"]).query).get("login_error") == [
        "login_failed"
    ]
    assert await count_sessions(admin_engine) == 1


# --- coming back --------------------------------------------------------------------------------


async def test_a_returning_user_keeps_their_id_and_gains_a_second_session(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    settings = db_config.settings()
    tokens = []
    async with logging_in(db_config, google) as (_, client):
        for _ in range(2):
            google.token_status, google.token_body = 200, None
            signed, attempt = await start_login(client, settings)
            google.id_token = mint_id_token(nonce=attempt.nonce)
            response = await finish_login(client, signed, attempt)
            tokens.append(cookie_from(response, "session"))

        both = [await client.get("/api/v1/me", headers={"Cookie": f"session={t}"}) for t in tokens]

    assert [r.status_code for r in both] == [200, 200]
    assert both[0].json()["id"] == both[1].json()["id"]  # one person, two devices
    async with admin_engine.connect() as connection:
        users = (await connection.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert users == 1
    assert await count_sessions(admin_engine) == 2


async def test_a_changed_google_email_updates_the_row_without_changing_the_user(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    """Identity is `sub`. An address change must not create a second person."""
    settings = db_config.settings()
    seen = []
    async with logging_in(db_config, google) as (_, client):
        for email in (EMAIL, "ada.new@example.test"):
            signed, attempt = await start_login(client, settings)
            google.id_token = mint_id_token(nonce=attempt.nonce, email=email)
            response = await finish_login(client, signed, attempt)
            token = cookie_from(response, "session")
            me = await client.get("/api/v1/me", headers={"Cookie": f"session={token}"})
            seen.append(me.json())

    assert seen[0]["id"] == seen[1]["id"]
    assert seen[1]["email"] == "ada.new@example.test"
    async with admin_engine.connect() as connection:
        count = (await connection.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert count == 1


async def test_last_login_at_moves_forward_on_a_return_visit(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    settings = db_config.settings()

    async def last_login() -> datetime:
        async with admin_engine.connect() as connection:
            value: datetime = (
                await connection.execute(text("SELECT last_login_at FROM users"))
            ).scalar_one()
            return value

    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        await finish_login(client, signed, attempt)
        first = await last_login()

        await asyncio.sleep(0.01)
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        await finish_login(client, signed, attempt)

    assert await last_login() > first


async def test_two_people_get_two_users(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        for sub, email in (("sub-one", "one@example.test"), ("sub-two", "two@example.test")):
            signed, attempt = await start_login(client, settings)
            google.id_token = mint_id_token(nonce=attempt.nonce, subject=sub, email=email)
            await finish_login(client, signed, attempt)

    async with admin_engine.connect() as connection:
        subs = (
            (await connection.execute(text("SELECT google_sub FROM users ORDER BY google_sub")))
            .scalars()
            .all()
        )
    assert subs == ["sub-one", "sub-two"]


# --- two callbacks at once ------------------------------------------------------------------------


async def test_simultaneous_first_logins_for_one_account_create_exactly_one_user(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    """Two tabs, two consents, one person: the upsert must not race into duplicate rows."""
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        starts = [await start_login(client, settings) for _ in range(4)]
        # Every attempt has its own nonce, so each needs its own token.
        google.id_token = None

        async def finish(signed: str, attempt: LoginAttempt) -> httpx.Response:
            google.id_token = mint_id_token(nonce=attempt.nonce)
            return await finish_login(client, signed, attempt)

        responses = await asyncio.gather(*(finish(s, a) for s, a in starts))

    assert all(r.status_code in (302, 307) for r in responses)
    async with admin_engine.connect() as connection:
        users = (await connection.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert users == 1
    assert await count_sessions(admin_engine) == 4  # one per consent, all independent


# --- housekeeping ---------------------------------------------------------------------------------


async def test_logging_in_clears_out_sessions_that_have_already_expired(
    db_config: DbConfig,
    google: FakeGoogle,
    admin_engine: AsyncEngine,
    make_user: MakeUser,
    session_factory: Any,
) -> None:
    settings = db_config.settings()
    stale_owner = await make_user(sub="someone-else", email="stale@example.test")
    await open_session(session_factory, stale_owner)
    async with admin_engine.begin() as connection:
        # `created_at` moves back too: the table forbids a session that expires before it began.
        await connection.execute(
            text(
                "UPDATE sessions SET created_at = now() - interval '8 days', "
                "expires_at = now() - interval '1 day'"
            )
        )
    live = await open_session(session_factory, stale_owner)
    assert await count_sessions(admin_engine) == 2

    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        await finish_login(client, signed, attempt)

        still_valid = await client.get("/api/v1/me", headers={"Cookie": f"session={live}"})

    # The expired one is gone; the live one (belonging to someone else) is untouched.
    assert await count_sessions(admin_engine) == 2
    assert still_valid.status_code == 200


# --- nothing is written unless the login really succeeded ---------------------------------------


@pytest.mark.parametrize(
    "break_it",
    [
        lambda g: g.token_endpoint_fails(400),
        lambda g: setattr(g, "id_token", mint_id_token(key=WRONG_KEY)),
        lambda g: setattr(g, "id_token", mint_id_token(nonce="another-login")),
        lambda g: setattr(g, "id_token", mint_id_token(email_verified=False)),
        lambda g: setattr(g, "token_failure", httpx.ConnectError("down")),
    ],
    ids=["exchange-refused", "bad-signature", "bad-nonce", "unverified-email", "google-down"],
)
async def test_a_failed_login_leaves_no_user_and_no_session(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine, break_it: Any
) -> None:
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        break_it(google)
        response = await finish_login(client, signed, attempt)

    failure = parse_qs(urlparse(response.headers["location"]).query)["login_error"]
    assert failure == ["login_failed"]
    async with admin_engine.connect() as connection:
        users = (await connection.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert users == 0
    assert await count_sessions(admin_engine) == 0


@pytest.mark.parametrize(
    "hostile",
    [
        {"next": "https://evil.example/steal"},
        {"redirect_uri": "https://evil.example"},
        {"returnTo": "//evil.example"},
        {"next": "javascript:alert(1)"},
    ],
    ids=["next", "redirect_uri", "returnTo", "javascript"],
)
async def test_a_SUCCESSFUL_login_still_ignores_any_redirect_the_caller_suggests(
    db_config: DbConfig, google: FakeGoogle, hostile: dict[str, str]
) -> None:
    """The success path is where an open redirect would actually be exploitable.

    A failed login has nothing worth stealing; a successful one has just issued a session cookie, so
    being bounced to an attacker's page is exactly when it hurts. The target comes from
    configuration alone, never from the query.
    """
    settings = db_config.settings()
    async with logging_in(db_config, google) as (_, client):
        signed, attempt = await start_login(client, settings)
        google.id_token = mint_id_token(nonce=attempt.nonce)
        response = await finish_login(client, signed, attempt, **hostile)

    assert cookie_from(response, "session") is not None  # the login really did succeed
    assert response.headers["location"] == "http://localhost:8000/"
    assert "evil.example" not in response.headers["location"]
    assert "javascript" not in response.headers["location"]
