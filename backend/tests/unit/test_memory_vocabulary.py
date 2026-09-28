"""The investor profile's fixed vocabulary (P13): what may be remembered, and how many."""

import re
from pathlib import Path

import pytest

from app.memory.vocabulary import CHOICES, FIELDS, SINGLE_VALUED, labels, valid_values

MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0009_memory.py"
).read_text(encoding="utf-8")


def test_every_field_has_choices_and_a_single_or_multi_rule() -> None:
    assert set(CHOICES) == set(FIELDS)
    assert {"risk_preference", "debt_preference"} == SINGLE_VALUED


@pytest.mark.parametrize(
    ("field", "values", "ok"),
    [
        ("risk_preference", ("conservative",), True),
        ("risk_preference", ("conservative", "aggressive"), False),  # one only
        ("risk_preference", ("reckless",), False),  # not in the vocabulary
        ("risk_preference", (), False),
        ("investment_style", ("income", "growth"), True),
        ("investment_style", ("income", "income"), False),  # distinct
        ("other_preferences", ("long_term", "stability"), True),
        ("debt_preference", ("avoid_high_debt",), True),
    ],
)
def test_values_must_be_known_distinct_and_the_right_count(
    field: str, values: tuple[str, ...], ok: bool
) -> None:
    assert valid_values(field, values) is ok  # type: ignore[arg-type]


def test_labels_follow_the_values() -> None:
    assert labels("investment_style", ("income", "growth")) == ("Dividends / income", "Growth")


def test_the_migrations_copy_of_choices_still_matches_the_vocabulary() -> None:
    """migration 0009 copies CHOICES literally rather than importing this module (a migration must
    not depend on application code that can change later). If the vocabulary changes, this test
    fails until the copy in a new migration is updated to match."""
    for field in FIELDS:
        found = re.search(rf"field = '{field}'\s*\n\s*AND tags <@ ARRAY\[([^\]]*)\]", MIGRATION)
        assert found is not None, f"no ARRAY[...] for {field} in migration 0009"
        copied = [value.strip().strip("'") for value in found.group(1).split(",")]
        assert copied == list(CHOICES[field])
