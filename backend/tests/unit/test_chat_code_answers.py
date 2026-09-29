"""Answers written by code (the owner's second review, 2026-09-29): valuation, where an answer's
figures came from, and whether a "why" answer gives a documented cause. Synthetic figures."""

from app.chat.answer_check import Claim
from app.chat.code_answers import (
    explained,
    previous_sources,
    source_claims,
    source_items,
    valuation_claims,
)
from app.chat.contract import Source, Turn, out_of_scope_text
from app.chat.evidence import EvidenceItem
from app.chat.understand import understand


def derived(item_id: str, symbol: str, label: str, amount: str, quote: str) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        kind="derived",
        symbol=symbol,
        text=f"{symbol} · {label} · {amount} · {quote}",
        source="derived",
        label=label,
        url=None,
        quote=quote,
        amount=amount,
    )


PE = derived(
    "D3",
    "TCS",
    "Price to earnings",
    "20.5",
    "Share price ₹2,050 (28 Sep 2026) divided by basic EPS of ₹100 for FY2026.",
)


def test_a_valuation_question_gets_the_stored_price_to_earnings_with_its_inputs() -> None:
    question = understand("Is TCS undervalued?", history=[])
    assert valuation_claims(question, [PE]) == [
        Claim(
            "TCS's price to earnings is 20.5: Share price ₹2,050 (28 Sep 2026) divided by basic "
            "EPS of ₹100 for FY2026.",
            ("D3",),
        )
    ]


def test_a_ratio_that_cannot_be_computed_says_why_and_other_stocks_are_left_out() -> None:
    missing = derived(
        "D4", "TCS", "Price to earnings", "not assessable", "Not assessable: no share price."
    )
    other = derived("D5", "RELIANCE", "Price to earnings", "25", "Reliance's ratio.")
    question = understand("Is TCS undervalued?", history=[])
    assert valuation_claims(question, [missing, other]) == [
        Claim("TCS's price to earnings cannot be computed: no share price.", ("D4",))
    ]


SOURCES = (
    Source(
        1, "filing", "Annual report · Annual Report 2025 · p.127", "https://bse/x.pdf#page=127", "q"
    ),
    Source(2, "screener", "screener.in · profit-loss · Sales · Mar 2025", "https://screener", None),
)


def test_where_an_answer_came_from_is_read_from_the_answer_itself() -> None:
    history = [
        Turn(role="user", text="What happened to revenue?"),
        Turn(role="assistant", text="It was ... [1][2]", sources=SOURCES),
        Turn(role="user", text="Thanks"),
    ]
    items = source_items(previous_sources(history))
    assert [(i.id, i.label, i.url) for i in items] == [
        ("S1", SOURCES[0].label, SOURCES[0].url),
        ("S2", SOURCES[1].label, SOURCES[1].url),
    ]
    assert [c.text for c in source_claims(items)] == [
        "Annual report · Annual Report 2025 · p.127 (official filing on BSE).",
        "screener.in · profit-loss · Sales · Mar 2025 (screener.in's fundamentals table).",
    ]


def test_the_latest_answer_counts_even_when_it_had_no_sources() -> None:
    history = [
        Turn(role="assistant", text="Earlier [1]", sources=SOURCES),
        Turn(role="assistant", text="I don't have that in the data."),
    ]
    assert previous_sources(history) == ()
    assert previous_sources([]) == ()


PASSAGE = EvidenceItem(
    id="N1",
    kind="passage",
    symbol="TCS",
    text="TCS · Annual report: revenue rose on large deals.",
    source="filing",
    label="Annual report · p.40",
    url=None,
    quote=None,
)
CHANGE = derived("D1", "TCS", "Revenue change", "5%", "Change.")


def test_a_why_answer_is_explained_only_by_a_cause_a_document_states() -> None:
    evidence = [PASSAGE, CHANGE]
    assert explained([Claim("Revenue grew 5%, driven by large deals.", ("D1", "N1"))], evidence)
    # a passage that states no cause, or a cause no document states
    assert not explained([Claim("The report called the year resilient.", ("N1",))], evidence)
    assert not explained([Claim("Revenue grew 5% due to demand.", ("D1",))], evidence)


def test_several_other_companies_are_named_together() -> None:
    assert out_of_scope_text("Infosys", "Wipro", "Apple").endswith(
        "I don't have grounded data for Infosys, Wipro or Apple."
    )
    assert out_of_scope_text(None).endswith("HDFC Bank.")
