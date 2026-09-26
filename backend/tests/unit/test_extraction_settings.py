"""The settings P11 adds: the extraction switch, the model and its prices, and the spending cap."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.helpers import build_settings


def test_extraction_is_off_unless_switched_on() -> None:
    """Like filings and fingerprints: a fresh checkout never calls the LLM or spends money."""
    assert build_settings().extraction_enabled is False


def test_the_model_is_nova_2_lite_through_the_global_profile() -> None:
    """ADR 010."""
    assert build_settings().llm_model == "global.amazon.nova-2-lite-v1:0"


def test_the_prices_are_nova_2_lites_in_us_dollars_per_million_tokens() -> None:
    settings = build_settings()
    assert settings.llm_input_usd_per_mtok == Decimal("0.35")
    assert settings.llm_output_usd_per_mtok == Decimal("2.95")


def test_the_spending_cap_is_the_two_dollars_the_owner_approved() -> None:
    assert build_settings().extraction_budget_usd == Decimal("2.00")


def test_two_calls_at_a_time() -> None:
    assert build_settings().extraction_concurrency == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_model": " "},
        {"extraction_budget_usd": -1},
        {"extraction_budget_usd": 101},
        {"llm_input_usd_per_mtok": -0.01},
        {"llm_output_usd_per_mtok": 0},
        {"extraction_concurrency": 0},
        {"extraction_concurrency": 9},
    ],
    ids=[
        "blank-model",
        "negative-cap",
        "huge-cap",
        "negative-price",
        "free-output",
        "none-at-once",
        "too-many",
    ],
)
def test_a_bad_value_is_refused_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        build_settings(**overrides)
