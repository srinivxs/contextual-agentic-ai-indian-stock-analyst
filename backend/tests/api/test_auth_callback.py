"""`GET /api/v1/auth/google/callback`: every way a login can fail.

The happy path needs a real database and lives in `tests/integration/test_auth_login_db.py`. What is
here is the part that matters most for security: each failure is refused, tells the browser nothing,
and **never reaches the database**. The session factory below fails the test if anything touches it,
so "no user or session was created" is proved structurally rather than by counting rows.
"""

import io
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, NoReturn
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI

from app.auth.login_state import new_login_attempt, sign_login_attempt
from app.auth.sessions import hash_session_token
from app.core.logging import JsonFormatter
from app.main import create_app
from tests.google_fakes import WRONG_KEY, FakeGoogle, mint_id_token
from tests.helpers import TEST_GOOGLE_CLIENT_SECRET, build_settings, parse_set_cookie

CALLBACK = "/api/v1/auth/google/callback"
LOGIN_COOKIE = "oauth_login"
CODE = "4/0Afake-authorization-code"


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("a failed login must never reach the database")


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def app(google: FakeGoogle) -> FastAPI:  # overrides the shared fixture
    from app.auth.jwks import JwksCache

    application = create_app(build_settings())
    application.state.session_factory = NoDatabase()
    application.state.http_client = google.client()
    application.state.jwks = JwksCache(client=google.client())
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


def signed_attempt(**overrides: str) -> tuple[str, str]:
    """A cookie for a login we really started, plus the `state` Google would hand back."""
    attempt = new_login_attempt()
    signed = sign_login_attempt(attempt, build_settings())
    return signed, overrides.get("state", attempt.state)


def assert_login_failed(response: httpx.Response, reason: str = "login_failed") -> None:
    """Every failure looks identical to the browser: a redirect carrying one opaque code."""
    assert response.status_code in (302, 307)
    target = urlparse(response.headers["location"])
    assert parse_qs(target.query)["login_error"] == [reason]
    assert response.headers["cache-control"] == "no-store"
    # Whatever went wrong, no session was handed out.
    cookies = [parse_set_cookie(h)[0] for h in response.headers.get_list("set-cookie")]
    assert "session" not in cookies
    assert "__Host-session" not in cookies


# --- the user said no -------------------------------------------------------------------------


async def test_a_cancelled_login_says_so_rather_than_looking_like_a_failure(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    signed, _ = signed_attempt()
    response = await client.get(
        CALLBACK,
        params={"error": "access_denied", "state": "anything"},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response, "cancelled")
    assert google.calls == 0


@pytest.mark.parametrize("error", ["invalid_request", "server_error", "temporarily_unavailable"])
async def test_any_other_error_from_google_is_a_generic_failure(
    client: httpx.AsyncClient, google: FakeGoogle, error: str
) -> None:
    signed, state = signed_attempt()
    response = await client.get(
        CALLBACK,
        params={"error": error, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)
    assert google.calls == 0


# --- state: checked BEFORE we talk to Google ---------------------------------------------------


async def test_a_mismatched_state_is_refused_without_ever_contacting_google(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    """The login-CSRF defence. An attacker's `code` must never be exchanged on a victim's behalf."""
    signed, _ = signed_attempt()
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": "the-attackers-own-state"},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)
    assert google.calls == 0  # the authorization code was never exchanged


async def test_a_callback_with_no_login_cookie_is_refused_without_contacting_google(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    """A link an attacker sent you: your browser has no attempt of its own to match it against."""
    response = await client.get(CALLBACK, params={"code": CODE, "state": "some-state"})
    assert_login_failed(response)
    assert google.calls == 0


@pytest.mark.parametrize(
    "corrupt",
    [lambda s: s[:-1], lambda s: s + "x", lambda s: "forged.cookie.value", lambda s: ""],
    ids=["truncated", "appended", "forged", "empty"],
)
async def test_a_tampered_login_cookie_is_refused_without_contacting_google(
    client: httpx.AsyncClient, google: FakeGoogle, corrupt: object
) -> None:
    signed, state = signed_attempt()
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={corrupt(signed)}"},  # type: ignore[operator]
    )
    assert_login_failed(response)
    assert google.calls == 0


async def test_a_callback_with_no_code_is_refused_without_contacting_google(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    signed, state = signed_attempt()
    response = await client.get(
        CALLBACK, params={"state": state}, headers={"Cookie": f"{LOGIN_COOKIE}={signed}"}
    )
    assert_login_failed(response)
    assert google.calls == 0


async def test_an_expired_login_attempt_is_refused(
    client: httpx.AsyncClient, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab left open past the login window cannot be completed."""
    import time

    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    signed, state = signed_attempt()
    clock["now"] += build_settings().oauth_login_ttl_seconds + 60

    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)
    assert google.calls == 0


# --- the exchange and the token ----------------------------------------------------------------


async def test_a_refused_exchange_ends_the_login(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    """A stolen, reused or expired code: Google refuses, and so do we."""
    signed, state = signed_attempt()
    google.token_endpoint_fails(400)
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)
    assert len(google.token_calls) == 1  # we did try, exactly once


async def test_a_slow_google_ends_the_login_rather_than_hanging_it(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    signed, state = signed_attempt()
    google.token_failure = httpx.ReadTimeout("timed out")
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)


async def test_an_id_token_signed_by_the_wrong_key_ends_the_login(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    signed, state = signed_attempt()
    google.id_token = mint_id_token(key=WRONG_KEY)
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)


async def test_an_id_token_carrying_someone_elses_nonce_ends_the_login(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    """Replay: a real, validly signed Google token that belongs to a different login attempt."""
    signed, state = signed_attempt()
    google.id_token = mint_id_token(nonce="a-nonce-from-another-login")
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)


async def test_an_unverified_email_ends_the_login(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    signed, state = signed_attempt()
    google.id_token = mint_id_token(email_verified=False)
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert_login_failed(response)


# --- redirect safety ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"next": "https://evil.example/steal"},
        {"redirect_uri": "https://evil.example"},
        {"returnTo": "//evil.example"},
        {"state": "x", "next": "javascript:alert(1)"},
    ],
    ids=["next", "redirect_uri", "returnTo", "javascript"],
)
async def test_the_callback_has_no_user_controlled_redirect_target(
    client: httpx.AsyncClient, extra: dict[str, str]
) -> None:
    """Whatever a caller adds to the query, we only ever send the browser to our own origin."""
    signed, state = signed_attempt()
    response = await client.get(
        CALLBACK,
        params={"code": CODE, "state": state, **extra},
        headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
    )
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("http://localhost:8000")
    assert "evil.example" not in response.headers["location"]


# --- what the outside world can learn ------------------------------------------------------------


async def test_every_failure_looks_the_same_to_the_browser(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    """Different causes, identical response: no oracle for an attacker to probe."""
    signed, state = signed_attempt()
    locations = set()
    breakages: tuple[Callable[[], Any], ...] = (
        lambda: google.token_endpoint_fails(400),
        lambda: setattr(google, "id_token", mint_id_token(key=WRONG_KEY)),
        lambda: setattr(google, "id_token", mint_id_token(nonce="other")),
    )

    for setup in breakages:
        google.token_status, google.token_body, google.id_token = 200, None, None
        setup()
        response = await client.get(
            CALLBACK,
            params={"code": CODE, "state": state},
            headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
        )
        locations.add(response.headers["location"])

    assert len(locations) == 1


async def test_nothing_sensitive_reaches_the_logs(
    client: httpx.AsyncClient, google: FakeGoogle
) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    # Only OUR loggers. The `httpx` records here belong to the test's own HTTP client logging the
    # request it just made to us, which is test scaffolding, not application behaviour. (In the
    # running app, httpx is only ever the *caller* of Google, and its POST puts nothing in the URL.)
    handler.addFilter(lambda record: record.name.startswith("app"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        attempt = new_login_attempt()
        signed = sign_login_attempt(attempt, build_settings())
        google.id_token = mint_id_token(key=WRONG_KEY)
        await client.get(
            CALLBACK,
            params={"code": CODE, "state": attempt.state},
            headers={"Cookie": f"{LOGIN_COOKIE}={signed}"},
        )
    finally:
        root.removeHandler(handler)

    logs = stream.getvalue()
    assert CALLBACK in logs  # the request really was logged...
    for secret in (
        CODE,
        attempt.state,
        attempt.nonce,
        attempt.code_verifier,
        signed,
        google.id_token,
        google.access_token,
        TEST_GOOGLE_CLIENT_SECRET,
        hash_session_token("x").hex(),
    ):
        assert secret not in logs  # ...and none of it carries any of this
    assert "code=" not in logs  # the query string is never logged
