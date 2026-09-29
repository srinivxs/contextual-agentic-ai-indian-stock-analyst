"""The settings the prices phase adds (ADR 025): off by default, and paced gently."""

import pytest
from pydantic import ValidationError

from tests.helpers import build_settings


def test_prices_are_off_and_paced_gently_by_default() -> None:
    settings = build_settings()
    assert settings.prices_enabled is False
    # BSE serves only about the last month of daily files (found 2026-09-29): ask for no more
    assert settings.prices_history_days == 30
    # gentler after the first real run: BSE refused the 7th file of a quick run
    assert settings.prices_per_run == 5
    assert settings.prices_pause_seconds == 30
    assert settings.prices_run_minutes == 5
    assert settings.prices_cooldown_minutes == 20


@pytest.mark.parametrize("minutes", [-1, 241])
def test_the_cool_down_is_bounded(minutes: int) -> None:
    with pytest.raises(ValidationError):
        build_settings(prices_cooldown_minutes=minutes)


def test_prices_are_a_deliberate_choice() -> None:
    assert build_settings(prices_enabled=True).prices_enabled is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"prices_history_days": 29},
        {"prices_history_days": 1101},
        {"prices_per_run": 0},
        {"prices_per_run": 51},
        {"prices_pause_seconds": -1},
        {"prices_pause_seconds": 121},
        {"prices_run_minutes": 0},
        {"prices_run_minutes": 1441},
    ],
)
def test_a_bad_value_is_refused_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        build_settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"prices_history_days": 30},
        {"prices_history_days": 1100},
        {"prices_per_run": 1},
        {"prices_per_run": 50},
        {"prices_pause_seconds": 0},
        {"prices_pause_seconds": 120},
        {"prices_run_minutes": 1},
        {"prices_run_minutes": 1440},
    ],
)
def test_the_limits_themselves_are_allowed(overrides: dict[str, object]) -> None:
    build_settings(**overrides)
