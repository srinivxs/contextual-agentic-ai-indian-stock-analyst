"""The settings P15 adds: which feed the worker reads, and how often."""

import pytest
from pydantic import ValidationError

from tests.helpers import build_settings


def test_the_feed_defaults_to_the_offline_fixtures() -> None:
    """Like every switch: a fresh checkout never reaches the internet."""
    assert build_settings().feed_mode == "fixture"


def test_live_mode_is_a_deliberate_choice() -> None:
    assert build_settings(feed_mode="live").feed_mode == "live"


def test_the_feed_is_polled_hourly_by_default() -> None:
    assert build_settings().feed_poll_minutes == 60


@pytest.mark.parametrize(
    "overrides",
    [
        {"feed_mode": "rss"},
        {"feed_mode": ""},
        {"feed_poll_minutes": 14},
        {"feed_poll_minutes": 1441},
        {"feed_poll_minutes": 0},
    ],
    ids=["unknown-mode", "blank-mode", "too-often", "too-rare", "never"],
)
def test_a_bad_value_is_refused_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        build_settings(**overrides)


@pytest.mark.parametrize("minutes", [15, 1440])
def test_the_limits_themselves_are_allowed(minutes: int) -> None:
    assert build_settings(feed_poll_minutes=minutes).feed_poll_minutes == minutes
