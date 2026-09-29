"""Which stock a press release names, and how it is tagged (P15): plain rules, no LLM.

Every company below is synthetic. The real alias table is only checked for its shape.
"""

import pytest

from app.feeds.tagging import ALIASES, stocks_named, tag
from app.vocabulary import EVENT_TYPES, IMPACTS, SENTIMENTS

DEMO = {
    "DEMO": ("DemoCo Limited", "DemoCo"),
    "ACME": ("Acme Bank & Co.", "ACM"),
}


def test_the_real_alias_table_covers_exactly_the_three_stocks() -> None:
    assert set(ALIASES) == {"RELIANCE", "TCS", "HDFCBANK"}
    assert all(names for names in ALIASES.values())


def test_a_named_company_is_found_case_insensitively() -> None:
    assert stocks_named("Penalty on DEMOCO limited", DEMO) == ("DEMO",)
    assert stocks_named("penalty on democo", DEMO) == ("DEMO",)


def test_several_companies_come_back_in_table_order_once_each() -> None:
    assert stocks_named("DemoCo and ACM and DemoCo Limited", DEMO) == ("DEMO", "ACME")


def test_a_name_inside_a_longer_word_is_not_a_match() -> None:
    assert stocks_named("SuperDemoCo and DemoCos and ACMEX", DEMO) == ()


def test_punctuation_around_a_name_still_matches() -> None:
    assert stocks_named("(DemoCo), ACM: notice", DEMO) == ("DEMO", "ACME")


def test_an_alias_with_special_characters_is_matched_literally() -> None:
    assert stocks_named("Order on Acme Bank & Co.", DEMO) == ("ACME",)
    assert stocks_named("Acme Bank X Co.", DEMO) == ()


@pytest.mark.parametrize("text", ["", "   ", "A press release about nobody"])
def test_nothing_named_gives_an_empty_tuple(text: str) -> None:
    assert stocks_named(text, DEMO) == ()


def test_an_empty_table_names_nobody() -> None:
    assert stocks_named("DemoCo", {}) == ()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Monetary penalty imposed on DemoCo", ("regulatory_legal", "negative", "medium")),
        ("Penalty on DemoCo for non-compliance", ("regulatory_legal", "negative", "medium")),
        ("RBI cancels licence of DemoCo", ("regulatory_legal", "negative", "high")),
        (
            "Cancellation of Certificate of Registration of DemoCo",
            ("regulatory_legal", "negative", "high"),
        ),
        ("Directions under Section 35A on DemoCo", ("regulatory_legal", "negative", "high")),
        ("Appointment of Managing Director at DemoCo", ("management_change", "neutral", "low")),
        ("Re-appointment of chairman of DemoCo", ("management_change", "neutral", "low")),
        ("RBI approves the scheme of DemoCo", ("regulatory_legal", "neutral", "low")),
        ("Approval granted to DemoCo", ("regulatory_legal", "neutral", "low")),
        ("Weekly statistical supplement", ("other", "neutral", "low")),
        ("", ("other", "neutral", "low")),
    ],
)
def test_titles_are_tagged_by_the_pattern_table(title: str, expected: tuple[str, str, str]) -> None:
    assert tag(title) == expected


def test_the_strongest_pattern_wins() -> None:
    """A licence cancellation that also mentions a penalty is high impact, not medium."""
    assert tag("Cancels licence and imposes penalty on DemoCo") == (
        "regulatory_legal",
        "negative",
        "high",
    )


def test_a_pattern_needs_whole_words() -> None:
    """'appointment' inside another word, or 'penalty' as part of a name, is not a signal."""
    assert tag("Disappointments in the quarter") == ("other", "neutral", "low")


def test_every_tag_comes_from_the_fixed_vocabulary() -> None:
    for title in ("penalty", "cancels licence", "appointment", "approval", "nothing"):
        event_type, sentiment, impact = tag(title)
        assert event_type in EVENT_TYPES
        assert sentiment in SENTIMENTS
        assert impact in IMPACTS
