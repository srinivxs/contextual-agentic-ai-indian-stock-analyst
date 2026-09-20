"""Authentication settings: the origin, cookie flags, cookie name and session lifetime."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.helpers import (
    TEST_DATABASE_URL,
    TEST_PUBLIC_BASE_URL,
    build_settings,
    production_settings,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "DATABASE_URL", "PUBLIC_BASE_URL", "COOKIE_SECURE", "SESSION_LIFETIME"):
        monkeypatch.delenv(name, raising=False)


def test_the_public_base_url_is_required() -> None:
    """The redirect target and the Origin check both need it; guessing it would be unsafe."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=TEST_DATABASE_URL)


@pytest.mark.parametrize(
    "url", ["localhost:8000", "ftp://example.test", "not a url", "", "javascript:alert(1)"]
)
def test_the_public_base_url_must_be_an_absolute_http_or_https_url(url: str) -> None:
    with pytest.raises(ValidationError):
        build_settings(public_base_url=url)


@pytest.mark.parametrize(
    ("configured", "origin"),
    [
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://localhost:8000/", "http://localhost:8000"),
        ("http://localhost:8000/some/path/", "http://localhost:8000"),
        ("HTTPS://App.Example.TEST/", "https://app.example.test"),
        ("https://app.example.test:443/", "https://app.example.test"),
    ],
)
def test_the_origin_is_scheme_host_and_port_only(configured: str, origin: str) -> None:
    """The Origin header of a browser request is compared against this exact string."""
    assert build_settings(public_base_url=configured).public_origin == origin


def test_the_session_lifetime_is_a_fixed_seven_days_by_default() -> None:
    assert build_settings().session_lifetime == timedelta(days=7)


def test_the_session_lifetime_can_be_configured_within_sane_bounds() -> None:
    assert build_settings(session_lifetime_hours=2).session_lifetime == timedelta(hours=2)
    for hours in (0, -5, 24 * 31):
        with pytest.raises(ValidationError):
            build_settings(session_lifetime_hours=hours)


def test_cookies_are_not_secure_by_default_outside_production() -> None:
    assert build_settings().cookie_secure is False


def test_production_refuses_to_start_without_secure_cookies() -> None:
    with pytest.raises(ValidationError):
        build_settings(
            app_env="production", public_base_url="https://app.example.test", cookie_secure=False
        )


def test_production_refuses_to_start_with_a_plain_http_origin() -> None:
    with pytest.raises(ValidationError):
        build_settings(
            app_env="production", public_base_url="http://app.example.test", cookie_secure=True
        )


def test_a_correct_production_configuration_is_accepted() -> None:
    settings = production_settings()
    assert settings.cookie_secure is True
    assert settings.public_origin == "https://app.example.test"


def test_local_development_may_use_plain_http() -> None:
    settings = build_settings(app_env="local", public_base_url=TEST_PUBLIC_BASE_URL)
    assert settings.cookie_secure is False


def test_only_production_uses_the_host_prefixed_cookie_name() -> None:
    """`__Host-` demands Secure, Path=/ and no Domain, so plain HTTP localhost cannot use it."""
    assert production_settings().session_cookie_name == "__Host-session"
    assert build_settings(app_env="local").session_cookie_name == "session"
    assert build_settings(app_env="test").session_cookie_name == "session"
