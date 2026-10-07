"""The worker's configuration: the shared part of Settings, and nothing about Google or sessions.

Least privilege: the worker never handles a login, so it must start without the Google client
secret or the session secret, and those must not even be fields it could read.
"""

import pytest
from pydantic import ValidationError

from app.core.config import CommonSettings, Settings
from tests.helpers import TEST_DATABASE_URL


def build(**overrides: object) -> CommonSettings:
    values: dict[str, object] = {"database_url": TEST_DATABASE_URL, **overrides}
    return CommonSettings(_env_file=None, **values)  # type: ignore[arg-type]


def test_the_worker_starts_with_only_a_database_url() -> None:
    settings = build()
    assert settings.worker_poll_seconds == 2.0
    assert settings.job_lease_seconds == 300


def test_filing_discovery_is_off_unless_switched_on() -> None:
    """ADR 018: the worker reaches screener.in and BSE only where the deployment says so."""
    settings = build()
    assert settings.filings_discovery is False
    assert settings.filings_refresh_hours == 24
    assert settings.filings_max_bytes == 60 * 1024 * 1024
    assert build(filings_discovery=True).filings_discovery is True


def test_the_worker_runs_two_ai_lanes_beside_the_web_lane_by_default() -> None:
    """One lane reaches BSE and screener.in, one request at a time; two lanes do everything else."""
    assert build().worker_ai_lanes == 2
    assert build(worker_ai_lanes=0).worker_ai_lanes == 0  # one job at a time, as before
    with pytest.raises(ValidationError):
        build(worker_ai_lanes=9)


def test_the_worker_settings_hold_no_login_secrets() -> None:
    for field in ("google_client_secret", "google_client_id", "session_secret", "public_base_url"):
        assert field not in CommonSettings.model_fields
        assert field in Settings.model_fields  # the API still has them


def test_the_api_settings_include_everything_the_worker_has() -> None:
    assert set(CommonSettings.model_fields) <= set(Settings.model_fields)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_poll_seconds", 0),
        ("worker_poll_seconds", 61),
        ("job_lease_seconds", 29),
        ("job_lease_seconds", 3601),
        ("filings_refresh_hours", 0),
        ("filings_refresh_hours", 169),
        ("filings_max_bytes", 1023),
        ("filings_max_bytes", 200 * 1024 * 1024 + 1),
    ],
)
def test_unreasonable_worker_timings_are_refused(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        build(**{field: value})


def test_the_database_driver_rule_applies_to_the_worker_too() -> None:
    with pytest.raises(ValidationError):
        build(database_url="postgresql://sync-driver/nope")


def test_the_worker_reads_its_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_worker_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("JOB_LEASE_SECONDS", "120")
    get_worker_settings.cache_clear()
    try:
        assert get_worker_settings().job_lease_seconds == 120
    finally:
        get_worker_settings.cache_clear()
