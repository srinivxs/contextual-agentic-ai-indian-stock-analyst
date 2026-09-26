"""Which pages of a filing the extractor LLM reads (P11), and how they are packed into windows.

Plain rules, no LLM: headline pages of an annual report, number-dense metric pages of
presentations and transcripts, the opening pages of a transcript or announcement for events.
All text is synthetic (DemoCo).
"""

import pytest

from app.page_selection import (
    EVENT_CHARS,
    MAX_ANNUAL_REPORT_PAGES,
    PAGE_TRUNCATED,
    Window,
    select_event_pages,
    select_fact_pages,
    windows,
)

EIGHT_NUMBERS = "10 20 30 40 50 60 70 80"
METRIC_PAGE = f"DemoCo revenue grew. Figures in ₹ crore: {EIGHT_NUMBERS}"


# --- annual reports: headline pages ---------------------------------------------------------------


@pytest.mark.parametrize(
    "headline",
    [
        "Consolidated Balance Sheet as at 31 March 2026",
        "Standalone Balance Sheet as at 31 March 2026",
        "Statement of Profit and Loss for the year ended 31 March 2026",
        "Statement of Profit and Loss for the period ended 30 September 2025",
        "Financial Highlights",
        "Key Indicators",
        "Key Financial Indicators",
        "Ten-year performance",
        "Ten year summary",
        "Dividend per share",
        "Dividend per equity share",
        "Net interest margin",
        "Gross NPA",
        "Key performance highlights",
    ],
)
def test_an_annual_report_page_with_a_headline_is_read(headline: str) -> None:
    pages = {1: "DemoCo chairman's letter", 2: f"DemoCo {headline} ..."}
    assert select_fact_pages("annual_report", pages) == [2]


def test_a_headline_split_across_lines_and_in_capitals_still_counts() -> None:
    pages = {7: "CONSOLIDATED BALANCE\nSHEET   AS AT 31 MARCH 2026"}
    assert select_fact_pages("annual_report", pages) == [7]


def test_an_annual_report_page_without_a_headline_is_skipped() -> None:
    # numbers and metric words alone do not qualify an annual-report page
    pages = {3: f"DemoCo revenue and profit in ₹ crore {EIGHT_NUMBERS}"}
    assert select_fact_pages("annual_report", pages) == []


def test_at_most_forty_annual_report_pages_keeping_the_ones_with_most_numbers() -> None:
    sparse = "Financial highlights 1"
    dense = "Financial highlights 1 2 3"
    pages = {n: sparse for n in range(1, 6)}  # pages 1..5 have one number
    pages.update({n: dense for n in range(6, 6 + MAX_ANNUAL_REPORT_PAGES)})
    chosen = select_fact_pages("annual_report", pages)
    assert chosen == list(range(6, 6 + MAX_ANNUAL_REPORT_PAGES))


def test_ties_on_numbers_keep_the_earlier_pages_and_return_page_order() -> None:
    pages = {n: "Financial highlights 1" for n in range(MAX_ANNUAL_REPORT_PAGES + 3, 0, -1)}
    assert select_fact_pages("annual_report", pages) == list(range(1, MAX_ANNUAL_REPORT_PAGES + 1))


# --- presentations and transcripts: number-dense metric pages ------------------------------------


@pytest.mark.parametrize("kind", ["presentation", "transcript"])
def test_a_metric_page_with_money_and_eight_numbers_is_read(kind: str) -> None:
    pages = {1: "DemoCo welcome", 2: METRIC_PAGE, 3: "Safe harbour statement"}
    assert select_fact_pages(kind, pages) == [2]


@pytest.mark.parametrize(
    "keyword",
    [
        "revenue",
        "profit",
        "PAT",
        "borrowings",
        "debt",
        "equity",
        "net worth",
        "dividend",
        "earnings per share",
        "EPS",
        "return on equity",
        "ROE",
        "net interest income",
        "net interest margin",
        "NIM",
        "NPA",
    ],
)
def test_every_metric_keyword_counts(keyword: str) -> None:
    pages = {1: f"DemoCo {keyword}: ₹ {EIGHT_NUMBERS}"}
    assert select_fact_pages("presentation", pages) == [1]


@pytest.mark.parametrize(
    "marker",
    ["₹", "Rs.", "INR", "crore", "crores", "Cr", "lakh", "US$", "USD", "million", "billion", "bn"],
)
def test_every_money_marker_counts(marker: str) -> None:
    pages = {1: f"DemoCo revenue {marker} {EIGHT_NUMBERS}"}
    assert select_fact_pages("presentation", pages) == [1]


def test_a_percent_sign_counts_as_a_marker() -> None:
    pages = {1: "DemoCo revenue growth 10% 20% 30% 40% 50% 60% 70% 80%"}
    assert select_fact_pages("transcript", pages) == [1]


def test_a_rupee_sign_extracted_as_the_letter_h_counts_as_money() -> None:
    pages = {1: "DemoCo revenue H12,345 and H 1,234; 3 4 5 6 7 8"}
    assert select_fact_pages("presentation", pages) == [1]


def test_an_ordinary_word_starting_with_h_is_not_money() -> None:
    pages = {1: f"DemoCo revenue from Hyderabad and Hubli {EIGHT_NUMBERS}"}
    assert select_fact_pages("presentation", pages) == []


def test_words_that_merely_contain_a_keyword_do_not_count() -> None:
    # "patent" is not PAT, "equitable" is not equity, "epsilon" is not EPS
    pages = {1: f"DemoCo patent equitable epsilon ₹ {EIGHT_NUMBERS}"}
    assert select_fact_pages("presentation", pages) == []


def test_words_that_merely_contain_a_marker_do_not_count() -> None:
    # "credit" is not Cr, "bank" is not bn, "firs" is not Rs
    pages = {1: f"DemoCo revenue credit bank firs {EIGHT_NUMBERS}"}
    assert select_fact_pages("presentation", pages) == []


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (f"DemoCo figures in ₹ {EIGHT_NUMBERS}", "no metric keyword"),
        (f"DemoCo revenue {EIGHT_NUMBERS}", "no money or percent marker"),
        ("DemoCo revenue ₹ 10 20 30 40 50 60 70", "only seven numbers"),
    ],
)
def test_a_page_missing_one_condition_is_skipped(text: str, why: str) -> None:
    assert select_fact_pages("presentation", {1: text}) == [], why


def test_a_number_with_commas_and_decimals_counts_once() -> None:
    seven = "DemoCo revenue ₹ 1,234.56 20 30 40 50 60 70"
    assert select_fact_pages("presentation", {1: seven}) == []
    assert select_fact_pages("presentation", {1: seven + " 80"}) == [1]


@pytest.mark.parametrize("kind", ["announcement", None, "something_else"])
def test_announcements_and_unknown_kinds_have_no_fact_pages(kind: str | None) -> None:
    assert select_fact_pages(kind, {1: METRIC_PAGE, 2: "Financial highlights"}) == []


def test_fact_pages_come_back_in_page_order() -> None:
    pages = {9: METRIC_PAGE, 2: METRIC_PAGE, 5: METRIC_PAGE}
    assert select_fact_pages("transcript", pages) == [2, 5, 9]


# --- event pages ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["transcript", "announcement"])
def test_event_pages_are_the_opening_pages_up_to_the_budget(kind: str) -> None:
    size = EVENT_CHARS // 3
    pages = {4: "d" * size, 2: "b" * size, 1: "a" * size, 3: "c" * size}
    assert select_event_pages(kind, pages) == [1, 2, 3]


def test_a_page_that_would_pass_the_budget_stops_the_selection() -> None:
    pages = {1: "a" * (EVENT_CHARS - 10), 2: "b" * 11, 3: "c"}
    assert select_event_pages("transcript", pages) == [1]


def test_the_first_page_is_always_read_even_when_it_is_long() -> None:
    pages = {1: "a" * (EVENT_CHARS * 2), 2: "b"}
    assert select_event_pages("announcement", pages) == [1]


def test_an_empty_document_has_no_event_pages() -> None:
    assert select_event_pages("transcript", {}) == []


@pytest.mark.parametrize("kind", ["annual_report", "presentation", None, "something_else"])
def test_other_kinds_have_no_event_pages(kind: str | None) -> None:
    assert select_event_pages(kind, {1: "DemoCo", 2: "more"}) == []


# --- windows --------------------------------------------------------------------------------------


def test_pages_are_packed_into_one_window_with_page_markers() -> None:
    pages = {1: "first", 2: "second", 3: "third"}
    assert windows(pages, [1, 3]) == [
        Window(pages=(1, 3), text="=== PAGE 1 ===\nfirst\n=== PAGE 3 ===\nthird")
    ]


def test_a_window_is_closed_before_it_would_pass_the_limit() -> None:
    block = "=== PAGE 1 ===\n" + "x" * 5  # 20 characters
    pages = {1: "x" * 5, 2: "y" * 5, 3: "z" * 5}
    exactly_two = 2 * len(block) + 1  # two blocks and the newline between them
    result = windows(pages, [1, 2, 3], max_chars=exactly_two)
    assert [w.pages for w in result] == [(1, 2), (3,)]
    assert all(len(w.text) <= exactly_two for w in result)
    assert len(result[0].text) == exactly_two


def test_a_page_longer_than_the_limit_goes_alone_and_is_truncated() -> None:
    pages = {1: "short", 2: "L" * 500, 3: "after"}
    result = windows(pages, [1, 2, 3], max_chars=100)
    assert [w.pages for w in result] == [(1,), (2,), (3,)]
    long_window = result[1]
    assert len(long_window.text) == 100
    assert long_window.text.startswith("=== PAGE 2 ===\nLLL")
    assert long_window.text.endswith("\n" + PAGE_TRUNCATED)


def test_a_long_first_page_does_not_leave_an_empty_window() -> None:
    result = windows({1: "L" * 500}, [1], max_chars=100)
    assert [w.pages for w in result] == [(1,)]


def test_selection_order_and_duplicates_do_not_change_the_windows() -> None:
    pages = {1: "one", 2: "two", 3: "three"}
    assert windows(pages, [3, 1, 2, 1]) == windows(pages, [1, 2, 3])


def test_no_selected_pages_means_no_windows() -> None:
    assert windows({1: "DemoCo"}, []) == []
