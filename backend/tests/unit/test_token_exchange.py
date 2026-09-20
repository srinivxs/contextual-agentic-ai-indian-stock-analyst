"""Trading the authorization code for an ID token: the one request that carries our client secret.

This is a server-to-server POST. The code came through the browser and is worthless on its own:
Google will only honour it for a client that authenticates with the matching secret AND presents the
PKCE verifier whose hash we sent at the start.
"""

from typing import Any

import httpx
import pytest

from app.auth.google import (
    GOOGLE_TOKEN_ENDPOINT,
    TokenExchangeFailed,
    exchange_code,
    redirect_uri,
)
from tests.google_fakes import FakeGoogle, mint_id_token
from tests.helpers import TEST_GOOGLE_CLIENT_ID, TEST_GOOGLE_CLIENT_SECRET, build_settings

CODE = "4/0Afake-authorization-code"
VERIFIER = "the-verifier-we-kept-on-the-server-side-only"


async def exchange(google: FakeGoogle, **kwargs: Any) -> str:
    async with google.client() as client:
        return await exchange_code(
            kwargs.pop("code", CODE),
            client=client,
            settings=kwargs.pop("settings", build_settings()),
            code_verifier=kwargs.pop("code_verifier", VERIFIER),
            **kwargs,
        )


# --- what we send -----------------------------------------------------------------------------


async def test_the_exchange_posts_exactly_the_fields_google_expects() -> None:
    google = FakeGoogle()
    await exchange(google)

    assert len(google.token_calls) == 1
    assert google.token_calls[0] == {
        "code": CODE,
        "client_id": TEST_GOOGLE_CLIENT_ID,
        "client_secret": TEST_GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri(build_settings()),
        "grant_type": "authorization_code",
        "code_verifier": VERIFIER,
    }


async def test_it_goes_to_googles_published_token_endpoint() -> None:
    expected = "https://oauth2.googleapis.com/token"
    assert expected == GOOGLE_TOKEN_ENDPOINT
    google = FakeGoogle()
    await exchange(google)
    assert google.token_calls  # the fake only records requests to /token


async def test_the_redirect_uri_must_match_the_one_used_at_the_authorization_step() -> None:
    """Google rejects the exchange if this differs by a single character."""
    google = FakeGoogle()
    settings = build_settings(public_base_url="http://localhost:8000/ignored/path/")
    await exchange(google, settings=settings)
    assert (
        google.token_calls[0]["redirect_uri"] == "http://localhost:8000/api/v1/auth/google/callback"
    )


async def test_the_verifier_is_sent_here_and_only_here() -> None:
    """It never appeared in the browser-facing authorization URL; only its hash did."""
    google = FakeGoogle()
    await exchange(google, code_verifier="a-specific-verifier-value")
    assert google.token_calls[0]["code_verifier"] == "a-specific-verifier-value"


# --- what we get back -------------------------------------------------------------------------


async def test_it_returns_the_id_token() -> None:
    google = FakeGoogle()
    google.id_token = mint_id_token()
    assert await exchange(google) == google.id_token


async def test_googles_access_and_refresh_tokens_are_discarded() -> None:
    """We call no Google API, so holding these would be a liability with no benefit."""
    google = FakeGoogle()
    google.refresh_token = "1//fake-refresh-token"  # noqa: S105
    returned = await exchange(google)
    assert returned == google.id_token or returned
    assert google.access_token not in returned
    assert google.refresh_token not in returned


# --- when it goes wrong -----------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_an_error_status_from_google_fails_the_exchange(status: int) -> None:
    google = FakeGoogle()
    google.token_endpoint_fails(status)
    with pytest.raises(TokenExchangeFailed):
        await exchange(google)


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "",
        "[]",
        "{}",
        '{"access_token": "only-this"}',
        '{"id_token": ""}',
        '{"id_token": null}',
        '{"id_token": 12345}',
    ],
    ids=[
        "not-json",
        "empty",
        "list",
        "no-id-token",
        "access-only",
        "blank",
        "null",
        "not-a-string",
    ],
)
async def test_a_response_without_a_usable_id_token_fails_the_exchange(body: str) -> None:
    google = FakeGoogle()
    google.token_status = 200
    google.token_body = body
    with pytest.raises(TokenExchangeFailed):
        await exchange(google)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("x"),
    ],
    ids=["timeout", "refused", "protocol"],
)
async def test_a_network_failure_fails_the_exchange(failure: Exception) -> None:
    """A slow or unreachable Google must end the login, never hang it."""
    google = FakeGoogle()
    google.token_failure = failure
    with pytest.raises(TokenExchangeFailed):
        await exchange(google)


async def test_the_failure_reveals_nothing_about_the_code_or_our_secret() -> None:
    google = FakeGoogle()
    google.token_endpoint_fails(400, '{"error":"invalid_grant","error_description":"Bad Request"}')
    with pytest.raises(TokenExchangeFailed) as caught:
        await exchange(google)
    message = str(caught.value)
    assert message == "token exchange failed"
    for secret in (CODE, VERIFIER, TEST_GOOGLE_CLIENT_SECRET, "invalid_grant"):
        assert secret not in message
