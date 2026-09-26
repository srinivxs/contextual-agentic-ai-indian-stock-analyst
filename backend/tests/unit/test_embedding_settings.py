"""The settings P10 adds: the embeddings switch, the region and model, and the spending cap."""

import pytest
from pydantic import ValidationError

from app.core.config import CommonSettings
from tests.helpers import TEST_DATABASE_URL, build_settings


def test_embeddings_are_off_unless_switched_on() -> None:
    """Off by default, like filing discovery: a fresh checkout never calls AWS or spends money."""
    assert build_settings().embeddings_enabled is False


def test_the_defaults_are_the_models_and_region_chosen_in_adr_010() -> None:
    settings = build_settings()
    assert settings.aws_region == "ap-south-1"
    assert settings.embedding_model == "amazon.titan-embed-text-v2:0"


def test_the_spending_cap_defaults_to_ten_million_tokens() -> None:
    """About $0.20 at Titan V2's price: twice the whole first run, the owner's cap is $1."""
    assert build_settings().embedding_token_budget == 10_000_000


def test_a_few_fingerprints_are_made_at_once() -> None:
    assert build_settings().embedding_concurrency == 4


@pytest.mark.parametrize(
    "overrides",
    [
        {"aws_region": "Mumbai"},
        {"aws_region": ""},
        {"embedding_model": " "},
        {"embedding_token_budget": -1},
        {"embedding_concurrency": 0},
        {"embedding_concurrency": 17},
    ],
    ids=[
        "region-name",
        "empty-region",
        "blank-model",
        "negative-budget",
        "none-at-once",
        "too-many",
    ],
)
def test_a_bad_value_is_refused_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        build_settings(**overrides)


def test_the_worker_has_the_same_settings_without_any_login_secret() -> None:
    worker = CommonSettings(_env_file=None, database_url=TEST_DATABASE_URL, embeddings_enabled=True)
    assert worker.embeddings_enabled is True
    assert worker.embedding_model == "amazon.titan-embed-text-v2:0"
    assert not hasattr(worker, "google_client_secret")
