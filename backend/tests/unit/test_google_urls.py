"""The authorization URL we redirect the browser to, and the redirect URI Google sends it back to.

This URL travels through the user's address bar, so it may contain nothing secret, and every
parameter has to be exactly right: Google matches `redirect_uri` character for character.
"""

from urllib.parse import parse_qs, urlparse

import pytest

from app.auth.google import (
    GOOGLE_AUTHORIZATION_ENDPOINT,
    GOOGLE_SCOPES,
    authorization_url,
    redirect_uri,
)
from app.auth.login_state import LoginAttempt, new_login_attempt
from app.auth.pkce import code_challenge
from tests.helpers import (
    TEST_GOOGLE_CLIENT_ID,
    TEST_GOOGLE_CLIENT_SECRET,
    TEST_SESSION_SECRET,
    build_settings,
    production_settings,
)

CALLBACK_PATH = "/api/v1/auth/google/callback"


def query_of(url: str) -> dict[str, str]:
    parsed = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert all(len(values) == 1 for values in parsed.values())  # no parameter is repeated
    return {key: values[0] for key, values in parsed.items()}


def built() -> tuple[str, dict[str, str], LoginAttempt]:
    settings, attempt = build_settings(), new_login_attempt()
    url = authorization_url(settings, attempt)
    return url, query_of(url), attempt


# --- the redirect URI ----------------------------------------------------------------------


def test_the_redirect_uri_is_our_origin_plus_the_callback_path() -> None:
    assert redirect_uri(build_settings()) == f"http://localhost:8000{CALLBACK_PATH}"


def test_the_redirect_uri_uses_the_origin_only_not_any_path_in_the_base_url() -> None:
    settings = build_settings(public_base_url="http://localhost:8000/some/path/")
    assert redirect_uri(settings) == f"http://localhost:8000{CALLBACK_PATH}"


def test_production_produces_an_https_redirect_uri() -> None:
    assert redirect_uri(production_settings()) == f"https://app.example.test{CALLBACK_PATH}"


def test_the_redirect_uri_has_no_query_or_fragment() -> None:
    """Google rejects a registered redirect URI containing a fragment, and matches exactly."""
    parsed = urlparse(redirect_uri(build_settings()))
    assert parsed.query == ""
    assert parsed.fragment == ""
    assert parsed.path == CALLBACK_PATH


# --- the authorization URL -----------------------------------------------------------------


def test_it_points_at_googles_published_authorization_endpoint() -> None:
    url, _, _ = built()
    assert url.startswith(GOOGLE_AUTHORIZATION_ENDPOINT + "?")
    assert GOOGLE_AUTHORIZATION_ENDPOINT == "https://accounts.google.com/o/oauth2/v2/auth"


def test_it_carries_exactly_the_parameters_we_intend() -> None:
    _, query, _ = built()
    assert set(query) == {
        "client_id",
        "redirect_uri",
        "response_type",
        "scope",
        "state",
        "nonce",
        "code_challenge",
        "code_challenge_method",
        "prompt",
    }


def test_the_fixed_parameters_have_the_right_values() -> None:
    _, query, _ = built()
    assert query["client_id"] == TEST_GOOGLE_CLIENT_ID
    assert query["redirect_uri"] == redirect_uri(build_settings())
    assert query["response_type"] == "code"  # authorization code flow, never an implicit one
    assert query["code_challenge_method"] == "S256"  # never "plain"
    assert query["prompt"] == "select_account"


def test_the_scope_is_only_openid_and_email() -> None:
    """We ask for the least Google will give us: an identity and an address. No profile, no APIs."""
    _, query, _ = built()
    assert query["scope"].split() == ["openid", "email"]
    assert GOOGLE_SCOPES == ("openid", "email")
    assert "offline" not in query.get("access_type", "")  # so Google issues no refresh token


def test_the_attempts_own_state_nonce_and_challenge_are_carried() -> None:
    _, query, attempt = built()
    assert query["state"] == attempt.state
    assert query["nonce"] == attempt.nonce
    assert query["code_challenge"] == code_challenge(attempt.code_verifier)


def test_the_verifier_itself_is_never_in_the_url() -> None:
    """Only the hash may travel through the browser; the verifier stays on our server."""
    url, _, attempt = built()
    assert attempt.code_verifier not in url


@pytest.mark.parametrize(
    ("label", "secret"),
    [("client secret", TEST_GOOGLE_CLIENT_SECRET), ("session secret", TEST_SESSION_SECRET)],
)
def test_no_secret_of_ours_appears_in_the_url(label: str, secret: str) -> None:
    url, _, _ = built()
    assert secret not in url, label
    assert "client_secret" not in url


def test_two_logins_never_reuse_state_or_nonce() -> None:
    settings = build_settings()
    urls = [query_of(authorization_url(settings, new_login_attempt())) for _ in range(50)]
    assert len({q["state"] for q in urls}) == 50
    assert len({q["nonce"] for q in urls}) == 50
    assert len({q["code_challenge"] for q in urls}) == 50


def test_every_value_is_percent_encoded_so_the_url_stays_well_formed() -> None:
    settings = build_settings()
    url = authorization_url(settings, new_login_attempt())
    assert " " not in url
    assert url.count("?") == 1
    assert "#" not in url
    # The redirect URI's own separators must be encoded inside the query string.
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000" in url
