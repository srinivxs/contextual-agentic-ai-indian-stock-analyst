"""The deterministic part of search (P10b): ranking the closest passages, and their excerpts.

The database finds the passages closest in meaning; these rules then decide the final order: a
small bonus for recent filings, at most two passages per document, and no passage twice.
"""

from dataclasses import replace
from datetime import date

import pytest

from app.retrieval import (
    MAX_PER_DOCUMENT,
    PROFILE_WEIGHT,
    RECENCY_WEIGHT,
    Passage,
    excerpt,
    filing_date,
    recency,
    rerank,
)

TODAY = date(2026, 9, 27)


def passage(chunk_id: int, similarity: float, **overrides: object) -> Passage:
    base = Passage(
        chunk_id=chunk_id,
        content_hash=f"{chunk_id:064x}",
        symbol="DEMO",
        document_id=chunk_id,
        title="DEMO earnings call transcript, Jan 2020",
        kind="transcript",
        period="Jan 2020",
        page=1,
        text=f"DemoCo passage {chunk_id}",
        source_url="https://www.bseindia.com/xml-data/corpfiling/AttachHis/x.pdf",
        similarity=similarity,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- when a filing is from ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("Jul 2026", date(2026, 7, 1)),
        ("Annual Report 2025", date(2025, 6, 30)),
        ("Financial Year 2024", date(2024, 6, 30)),
        ("Board meeting on results", None),  # an announcement's subject has no date
        (None, None),
        ("Foo 2026", date(2026, 6, 30)),  # not a month: the year alone
    ],
)
def test_the_date_of_a_filing_comes_from_its_period(
    period: str | None, expected: date | None
) -> None:
    assert filing_date(period) == expected


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("Jul 2026", 1.0),  # within a year
        ("Jan 2025", 0.5),  # within two
        ("Jan 2023", 0.0),  # older
        ("Board meeting", 0.0),  # unknown: no bonus, never a penalty
    ],
)
def test_recent_filings_score_higher(period: str, expected: float) -> None:
    assert recency(period, TODAY) == expected


# --- the order ------------------------------------------------------------------------------------


def test_the_closest_in_meaning_comes_first() -> None:
    results = rerank([passage(1, 0.40), passage(2, 0.70), passage(3, 0.55)], today=TODAY, k=5)
    assert [r.passage.chunk_id for r in results] == [2, 3, 1]


def test_a_recent_filing_wins_a_near_tie_but_not_a_clear_gap() -> None:
    old_but_closer = passage(1, 0.600, period="Jan 2020")
    recent = passage(2, 0.590, period="Jul 2026")
    clearly_closer = passage(3, 0.700, period="Jan 2020")

    results = rerank([old_but_closer, recent, clearly_closer], today=TODAY, k=5)

    assert [r.passage.chunk_id for r in results] == [3, 2, 1]
    assert results[1].score == pytest.approx(0.590 + RECENCY_WEIGHT)


def test_a_passage_close_to_the_investor_s_profile_wins_a_near_tie_but_not_a_clear_gap() -> None:
    """P13: the profile's fingerprint nudges the order among passages already close to the
    question, as recency does; it never brings in a passage the question did not."""
    plain = passage(1, 0.600)
    near_profile = passage(2, 0.590, profile_similarity=0.9)
    clearly_closer = passage(3, 0.700)

    results = rerank([plain, near_profile, clearly_closer], today=TODAY, k=5)

    assert [r.passage.chunk_id for r in results] == [3, 2, 1]
    assert results[1].score == pytest.approx(0.590 + PROFILE_WEIGHT * 0.9)


def test_without_a_profile_or_with_an_opposite_one_there_is_no_bonus() -> None:
    assert passage(1, 0.5).profile_similarity == 0.0
    [result] = rerank([passage(1, 0.5, profile_similarity=-0.4)], today=TODAY, k=5)
    assert result.score == pytest.approx(0.5)


def test_one_document_cannot_fill_every_slot() -> None:
    same_document = [passage(n, 0.9 - n / 100, document_id=7) for n in range(1, 5)]
    other = passage(9, 0.5, document_id=8)

    results = rerank([*same_document, other], today=TODAY, k=5)

    assert [r.passage.chunk_id for r in results] == [1, 2, 9]
    assert MAX_PER_DOCUMENT == 2


def test_the_same_paragraph_in_two_filings_appears_once() -> None:
    first = passage(1, 0.8, content_hash="aa" * 32, document_id=1)
    repeat = passage(2, 0.8, content_hash="aa" * 32, document_id=2)

    results = rerank([first, repeat], today=TODAY, k=5)

    assert [r.passage.chunk_id for r in results] == [1]


def test_at_most_k_results_and_ties_break_the_same_way_every_time() -> None:
    tied = [passage(n, 0.5, document_id=n) for n in (5, 3, 4, 1, 2)]
    results = rerank(tied, today=TODAY, k=3)
    assert [r.passage.chunk_id for r in results] == [1, 2, 3]


def test_nothing_in_gives_nothing_out() -> None:
    assert rerank([], today=TODAY, k=5) == []


# --- the excerpt ----------------------------------------------------------------------------------


def test_a_short_passage_is_shown_whole() -> None:
    assert excerpt("Revenue rose 12%.") == "Revenue rose 12%."


def test_a_long_passage_is_cut_at_a_word_near_300_characters() -> None:
    text = "word " * 100  # 500 characters
    cut = excerpt(text)
    assert len(cut) <= 301
    assert cut.endswith("…")
    assert not cut[:-1].endswith(" ")
    assert "wor…" not in cut  # never in the middle of a word


def test_a_passage_with_no_spaces_to_cut_at_is_cut_at_the_limit() -> None:
    """A long table row or URL-like run: better cut mid-token than shown whole."""
    cut = excerpt("x" * 500)
    assert cut == "x" * 300 + "…"


def test_spaces_and_line_breaks_are_tidied() -> None:
    assert excerpt("Revenue\n\n  rose   12%.") == "Revenue rose 12%."
