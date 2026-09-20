"""The exact Set-Cookie headers we send, in development and in production."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import pytest
from starlette.responses import Response

from app.auth.cookies import clear_session_cookie, set_session_cookie
from app.core.config import Settings
from tests.helpers import build_settings, parse_set_cookie, production_settings

RAW = "raw-session-value-for-header-tests"  # not a real token; only the header shape matters


def one_cookie(response: Response) -> tuple[str, str, dict[str, str]]:
    headers = response.headers.getlist("set-cookie")
    assert len(headers) == 1
    name, value, attributes = parse_set_cookie(headers[0])
    return name, value.strip('"'), attributes


def test_the_development_cookie_is_plain_session_httponly_and_lax() -> None:
    settings = build_settings()
    response = Response()
    set_session_cookie(response, RAW, settings)

    name, value, attributes = one_cookie(response)
    assert (name, value) == ("session", RAW)
    assert "httponly" in attributes  # JavaScript can never read it
    assert attributes["samesite"].lower() == "lax"
    assert attributes["path"] == "/"
    assert attributes["max-age"] == str(7 * 24 * 3600)  # the fixed seven-day lifetime
    assert "secure" not in attributes  # plain-HTTP localhost could not store a Secure cookie
    assert "domain" not in attributes  # host-only


def test_the_production_cookie_meets_every_host_prefix_rule() -> None:
    """`__Host-` cookies are rejected by the browser unless ALL of these hold."""
    response = Response()
    set_session_cookie(response, RAW, production_settings())

    name, value, attributes = one_cookie(response)
    assert (name, value) == ("__Host-session", RAW)
    assert "secure" in attributes
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "lax"
    assert attributes["path"] == "/"
    assert "domain" not in attributes  # a Domain attribute would make the browser drop it


def test_the_lifetime_setting_controls_the_cookie_max_age() -> None:
    response = Response()
    set_session_cookie(response, RAW, build_settings(session_lifetime_hours=2))
    assert one_cookie(response)[2]["max-age"] == "7200"


def test_the_token_appears_only_in_the_cookie_value() -> None:
    response = Response()
    set_session_cookie(response, RAW, build_settings())
    headers = response.headers.getlist("set-cookie")
    assert sum(RAW in h for h in headers) == 1
    others = [v for k, v in response.headers.items() if k.lower() != "set-cookie"]
    assert all(RAW not in value for value in others)  # no other header carries it


@pytest.mark.parametrize(
    ("settings", "cookie_name", "secure"),
    [
        (build_settings(), "session", False),
        (production_settings(), "__Host-session", True),
    ],
    ids=["development", "production"],
)
def test_clearing_expires_the_cookie_with_the_same_attributes(
    settings: Settings, cookie_name: str, secure: bool
) -> None:
    """A clearing header must obey the same prefix rules, or the browser would ignore it."""
    response = Response()
    clear_session_cookie(response, settings)

    name, value, attributes = one_cookie(response)
    assert name == cookie_name
    assert value == ""
    assert attributes["max-age"] == "0"
    assert attributes["path"] == "/"
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "lax"
    assert ("secure" in attributes) is secure
    assert "domain" not in attributes
    expires = parsedate_to_datetime(attributes["expires"])
    assert expires < datetime.now(UTC)  # already in the past
