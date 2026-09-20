"""Configuration Google sign-in needs.

All of it is required: a deployment that cannot complete a login should fail at startup with a
clear message, not at the moment a user clicks "Sign in".
"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.helpers import (
    TEST_DATABASE_URL,
    TEST_GOOGLE_CLIENT_ID,
    TEST_GOOGLE_CLIENT_SECRET,
    TEST_PUBLIC_BASE_URL,
    TEST_SESSION_SECRET,
    build_settings,
)

SHORT_SECRET = "too-short"  # noqa: S105  (a deliberately invalid value, not a credential)

REQUIRED: dict[str, Any] = {
    "database_url": TEST_DATABASE_URL,
    "public_base_url": TEST_PUBLIC_BASE_URL,
    "google_client_id": TEST_GOOGLE_CLIENT_ID,
    "google_client_secret": TEST_GOOGLE_CLIENT_SECRET,
    "session_secret": TEST_SESSION_SECRET,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "SESSION_SECRET",
        "OAUTH_LOGIN_TTL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("missing", ["google_client_id", "google_client_secret", "session_secret"])
def test_each_google_setting_is_required(missing: str) -> None:
    values = {key: value for key, value in REQUIRED.items() if key != missing}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_all_of_them_together_are_accepted() -> None:
    settings = build_settings()
    assert settings.google_client_id == TEST_GOOGLE_CLIENT_ID
    assert settings.google_client_secret.get_secret_value() == TEST_GOOGLE_CLIENT_SECRET
    assert settings.session_secret.get_secret_value() == TEST_SESSION_SECRET


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_client_id_is_rejected(blank: str) -> None:
    with pytest.raises(ValidationError):
        build_settings(google_client_id=blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_client_secret_is_rejected(blank: str) -> None:
    with pytest.raises(ValidationError):
        build_settings(google_client_secret=blank)


def test_a_short_session_secret_is_rejected() -> None:
    """It keys the signature on the login cookie; a guessable one lets anyone forge state."""
    with pytest.raises(ValidationError):
        build_settings(session_secret=SHORT_SECRET)
    assert build_settings(session_secret="x" * 32).session_secret  # 32 characters is the floor


@pytest.mark.parametrize("field", ["google_client_secret", "session_secret"])
def test_secrets_never_appear_in_repr_or_str(field: str) -> None:
    """Settings objects get logged and appear in tracebacks: the values must not be in them."""
    canary = "canary-secret-value-" + "y" * 32
    settings = build_settings(**{field: canary})
    assert canary not in repr(settings)
    assert canary not in str(settings)
    assert getattr(settings, field).get_secret_value() == canary


def test_a_rejected_secret_is_not_echoed_into_the_validation_error() -> None:
    """The startup crash message goes to the logs, so it must not carry the value it rejected."""
    canary = "short-canary"
    with pytest.raises(ValidationError) as caught:
        build_settings(session_secret=canary)
    assert canary not in str(caught.value)


def test_the_client_id_is_not_treated_as_a_secret() -> None:
    """It is published in every authorization URL, so hiding it would be theatre."""
    assert TEST_GOOGLE_CLIENT_ID in repr(build_settings())


def test_the_login_window_defaults_to_ten_minutes_and_has_sane_bounds() -> None:
    assert build_settings().oauth_login_ttl_seconds == 600
    assert build_settings(oauth_login_ttl_seconds=60).oauth_login_ttl_seconds == 60
    for bad in (0, -1, 59, 3601):
        with pytest.raises(ValidationError):
            build_settings(oauth_login_ttl_seconds=bad)


def test_the_google_http_timeout_defaults_to_a_few_seconds() -> None:
    """P4b-3 uses it for the token endpoint; a login must not hang on a slow Google."""
    assert 1 <= build_settings().google_timeout_seconds <= 10
    for bad in (0, -1, 121):
        with pytest.raises(ValidationError):
            build_settings(google_timeout_seconds=bad)
