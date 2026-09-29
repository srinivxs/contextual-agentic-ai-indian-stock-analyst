"""What the stock page shows, computed from stored rows (P11c): pure functions, no database.

Key facts: the winning figure for each metric and fiscal year (app/derived.py's choose_fact), in
one view per metric (consolidated ₹ first), the latest three years, each with its citation.
Derived values and sentiment are computed on read (ADR 009) and cite the facts they used.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.derived import EventRow, FactRow
from app.insights import (
    Citation,
    StoredEvent,
    StoredFact,
    change_views,
    derived_views,
    filing_citation,
    key_facts,
    measure_facts,
    recent_events,
    reported_label,
    screener_citation,
)

BSE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/demo.pdf"
SCREENER = "https://www.screener.in/company/DEMO/consolidated/"


def fact(
    fact_id: int,
    metric: str,
    period: str,
    value: str,
    *,
    basis: str = "consolidated",
    currency: str | None = "INR",
    unit: str = "INR_CRORE",
    source: str = "annual_report",
    page: int = 10,
) -> StoredFact:
    year = int(period[-4:])
    return StoredFact(
        row=FactRow(
            id=fact_id,
            metric=metric,
            period=period,
            period_end=date(year, 3, 31),
            basis=basis,  # type: ignore[arg-type]
            currency=currency,  # type: ignore[arg-type]
            unit=unit,
            value=Decimal(value),
            source=source,  # type: ignore[arg-type]
            source_date=date(year, 6, 30),
        ),
        citation=filing_citation(
            "annual_report", f"Annual Report {year}", "DemoCo AR", BSE, page, f"quote {fact_id}"
        ),
    )


# --- citations ------------------------------------------------------------------------------------


def test_a_filing_is_cited_by_kind_period_and_page_and_links_to_that_page() -> None:
    citation = filing_citation("transcript", "Jul 2026", "DEMO call", BSE, 7, "Revenue rose.")
    assert citation == Citation(
        source="filing",
        label="Earnings call · Jul 2026 · p.7",
        url=f"{BSE}#page=7",
        quote="Revenue rose.",
    )


def test_a_filing_without_a_kind_or_address_falls_back_to_its_title() -> None:
    citation = filing_citation(None, None, "DemoCo filing", None, 3, "q")
    assert (citation.label, citation.url) == ("DemoCo filing · p.3", None)


def test_screener_is_cited_by_section_row_and_column() -> None:
    assert screener_citation(SCREENER, "profit-loss", "Net Profit", "Mar 2026") == Citation(
        source="screener",
        label="screener.in · profit-loss · Net Profit · Mar 2026",
        url=SCREENER,
        quote=None,
    )


# --- key facts ------------------------------------------------------------------------------------


def test_key_facts_are_the_latest_three_years_newest_first_in_vocabulary_order() -> None:
    facts = [
        fact(1, "net_profit", "FY2023", "80"),
        fact(2, "net_profit", "FY2024", "90"),
        fact(3, "net_profit", "FY2025", "100"),
        fact(4, "net_profit", "FY2026", "110"),
        fact(5, "revenue_from_operations", "FY2026", "900"),
    ]

    shown = key_facts(facts)

    assert [(f.metric, f.period) for f in shown] == [
        ("revenue_from_operations", "FY2026"),
        ("net_profit", "FY2026"),
        ("net_profit", "FY2025"),
        ("net_profit", "FY2024"),
    ]
    assert shown[0].label == "Revenue from operations"
    assert shown[0].citation.url == f"{BSE}#page=10"


def test_one_view_per_metric_consolidated_rupees_first() -> None:
    facts = [
        fact(1, "net_profit", "FY2026", "50", basis="standalone"),
        fact(2, "net_profit", "FY2026", "110"),
        fact(3, "net_profit", "FY2026", "12", currency="USD", unit="USD_MILLION"),
    ]
    [shown] = key_facts(facts)
    assert (shown.basis, shown.currency, shown.value) == ("consolidated", "INR", Decimal("110"))


def test_a_dollar_figure_is_shown_as_reported_when_it_is_the_only_one() -> None:
    [shown] = key_facts(
        [fact(1, "revenue_from_operations", "FY2026", "1800", currency="USD", unit="USD_MILLION")]
    )
    assert (shown.currency, shown.unit, shown.value) == ("USD", "USD_MILLION", Decimal("1800"))


def test_quarters_are_not_key_facts() -> None:
    assert key_facts([fact(1, "net_profit", "Q3FY2026", "30")]) == []


def test_a_disagreement_is_shown_with_both_sources() -> None:
    facts = [
        fact(1, "net_profit", "FY2026", "110", source="annual_report"),
        fact(2, "net_profit", "FY2026", "130", source="presentation", page=4),
    ]
    [shown] = key_facts(facts)
    assert (shown.status, shown.value) == ("disputed", Decimal("110"))
    assert [c.label for c in shown.disputed_by] == ["Annual report · Annual Report 2026 · p.4"]


def test_agreeing_sources_are_counted() -> None:
    facts = [fact(1, "net_profit", "FY2026", "110"), fact(2, "net_profit", "FY2026", "110.5")]
    [shown] = key_facts(facts)
    assert (shown.status, shown.corroborated_by, shown.disputed_by) == ("agreed", 1, ())


# --- derived values -------------------------------------------------------------------------------


def test_all_four_derived_values_come_back_in_order_with_their_citations() -> None:
    facts = [
        fact(1, "total_borrowings", "FY2026", "250"),
        fact(2, "total_equity", "FY2026", "1000"),
        fact(3, "revenue_from_operations", "FY2025", "800"),
        fact(4, "revenue_from_operations", "FY2026", "900"),
    ]

    views = derived_views(facts, is_financial=False)

    assert [v.name for v in views] == [
        "debt_to_equity",
        "revenue_growth",
        "profit_growth",
        "latest_dividend",
    ]
    debt = views[0]
    assert (debt.status, debt.value) == ("ok", Decimal("0.25"))
    assert [c.quote for c in debt.citations] == ["quote 1", "quote 2"]
    assert (views[1].status, views[1].value) == ("ok", Decimal("12.5"))
    assert views[2].status == "not_assessable"
    assert views[3].status == "insufficient_data"
    assert all(v.reason for v in views)


def test_a_bank_grows_by_net_interest_income_and_has_no_debt_to_equity() -> None:
    facts = [
        fact(1, "net_interest_income", "FY2025", "100"),
        fact(2, "net_interest_income", "FY2026", "110"),
    ]
    views = derived_views(facts, is_financial=True)
    assert views[0].status == "not_applicable"
    assert (views[1].label, views[1].status, views[1].value) == (
        "Net interest income growth",
        "ok",
        Decimal("10.0"),
    )


# --- events ---------------------------------------------------------------------------------------


def event(event_id: int, day: date) -> StoredEvent:
    return StoredEvent(
        row=EventRow(event_id, "dividend", "positive", "low", day),
        summary=f"Event {event_id}",
        citation=filing_citation("announcement", "Dividend", "DemoCo", BSE, 1, "q"),
    )


def test_recent_events_are_newest_first_and_at_most_ten() -> None:
    events = [event(n, date(2026, 1, n)) for n in range(1, 13)]
    shown = recent_events(events)
    assert [e.row.id for e in shown] == list(range(12, 2, -1))


def test_amounts_leave_the_api_as_plain_digits() -> None:
    from app.api.insights import _amount

    assert _amount(Decimal("1E+2")) == "100"
    assert _amount(Decimal("-3.0")) == "-3.0"
    assert _amount(None) is None


# --- one measure for one answer (the owner's review, 2026-09-29) --------------------------------

REVENUE = "revenue_from_operations"


def screener(fact_id: int, period: str, value: str, **kwargs: str) -> StoredFact:
    stored = fact(fact_id, REVENUE, period, value, source="screener", **kwargs)  # type: ignore[arg-type]
    year = period[-4:]
    citation = screener_citation(SCREENER, "profit-loss", "Sales", f"Mar {year}")
    return StoredFact(row=stored.row, citation=citation)


# The annual reports have two years, screener.in three; they count revenue a little differently.
TWO_SOURCES = [
    fact(1, REVENUE, "FY2025", "980"),
    fact(2, REVENUE, "FY2024", "914"),
    screener(3, "FY2026", "1055"),
    screener(4, "FY2025", "962"),
    screener(5, "FY2024", "899"),
]


def shown(facts: list[StoredFact], **kwargs: object) -> list[tuple[str, str, str]]:
    return [
        (f.period, str(f.value), f.citation.source)
        for f in measure_facts(facts, REVENUE, **kwargs)  # type: ignore[arg-type]
    ]


def test_the_latest_years_come_from_the_best_ranked_source_that_has_them_all() -> None:
    # the annual reports lack FY2026, so all three years come from screener.in: like with like
    assert shown(TWO_SOURCES) == [
        ("FY2026", "1055", "screener"),
        ("FY2025", "962", "screener"),
        ("FY2024", "899", "screener"),
    ]


def test_a_named_year_takes_the_best_ranked_source_that_has_it_and_names_the_other() -> None:
    [only] = measure_facts(TWO_SOURCES, REVENUE, periods=("FY2024",))
    assert (only.value, only.citation.source, only.status) == (Decimal("914"), "filing", "disputed")
    assert [(r.citation.source, r.value) for r in only.rivals] == [("screener", Decimal("899"))]
    assert only.row is not None
    assert only.row.id == 2


def test_two_named_years_come_from_one_source() -> None:
    assert shown(TWO_SOURCES, periods=("FY2024", "FY2025")) == [
        ("FY2025", "980", "filing"),
        ("FY2024", "914", "filing"),
    ]


def test_a_figure_used_for_consistency_names_the_better_ranked_one_it_differs_from() -> None:
    fy2025 = measure_facts(TWO_SOURCES, REVENUE)[1]
    assert [(r.citation.source, r.value) for r in fy2025.rivals] == [("filing", Decimal("980"))]


def test_when_no_source_has_every_year_each_year_takes_its_best_figure() -> None:
    facts = [fact(1, REVENUE, "FY2025", "980"), screener(2, "FY2024", "899")]
    assert shown(facts) == [("FY2025", "980", "filing"), ("FY2024", "899", "screener")]


def test_a_figure_within_one_percent_agrees_and_is_no_rival() -> None:
    facts = [fact(1, REVENUE, "FY2025", "1000"), screener(2, "FY2025", "1005")]
    [only] = measure_facts(facts, REVENUE)
    assert (only.status, only.corroborated_by, only.rivals) == ("agreed", 1, ())


def test_the_window_of_years_and_a_basis_the_question_asks_for() -> None:
    facts = [*TWO_SOURCES, fact(9, REVENUE, "FY2026", "524", basis="standalone")]
    assert shown(facts, years=1) == [("FY2026", "1055", "screener")]
    assert shown(facts, years=1, basis="standalone") == [("FY2026", "524", "filing")]
    # a basis with no figures falls back to the usual order
    assert shown(TWO_SOURCES, years=1, basis="standalone") == [("FY2026", "1055", "screener")]


def test_a_named_year_nobody_has_gives_nothing() -> None:
    assert measure_facts(TWO_SOURCES, REVENUE, periods=("FY2019",)) == []
    assert measure_facts(TWO_SOURCES, "net_profit") == []


def test_changes_are_computed_between_consecutive_years_of_one_source() -> None:
    views = change_views(measure_facts(TWO_SOURCES, REVENUE))
    assert [(v.label, v.status, str(v.value)) for v in views] == [
        ("Revenue from operations change, FY2025 to FY2026", "ok", "9.7"),
        ("Revenue from operations change, FY2024 to FY2025", "ok", "7.0"),
    ]
    latest = views[0]
    assert latest.name == "change"
    assert latest.reason == (
        "Change in revenue from operations from FY2025 to FY2026, consolidated figures from "
        "screener.in."
    )
    assert [(row.period, row.value) for row in latest.inputs] == [
        ("FY2025", Decimal("962")),
        ("FY2026", Decimal("1055")),
    ]
    assert len(latest.citations) == 2


def test_no_change_is_computed_across_two_sources_or_from_a_year_at_zero() -> None:
    mixed = [fact(1, REVENUE, "FY2025", "980"), screener(2, "FY2024", "899")]
    assert change_views(measure_facts(mixed, REVENUE)) == []
    zero = [fact(1, REVENUE, "FY2025", "980"), fact(2, REVENUE, "FY2024", "0")]
    [view] = change_views(measure_facts(zero, REVENUE))
    assert (view.status, view.value) == ("not_assessable", None)
    assert "zero or negative" in view.reason


def test_derived_values_carry_the_stored_figures_they_were_computed_from() -> None:
    facts = [
        fact(1, "total_borrowings", "FY2026", "50"),
        fact(2, "total_equity", "FY2026", "200"),
    ]
    debt = derived_views(facts, is_financial=False)[0]
    assert [row.id for row in debt.inputs] == [1, 2]


def test_no_change_is_computed_from_figures_without_their_stored_rows() -> None:
    # the stock page's key facts carry no row: a change needs the rows' source and basis
    assert change_views(key_facts(TWO_SOURCES)) == []


# --- what each source calls its figure (the owner's second review) --------------------------------


@pytest.mark.parametrize(
    ("quote", "label"),
    [
        ("Revenue from Operations 25 9,80,136 9,14,472", "Revenue from Operations"),
        ("Profit for the year 81,309 79,020", "Profit for the year"),
        ("PAT at H 95,754 crore (US$ 10.1 billion), was up 17.8%", "PAT"),
        (
            "Net Profit Attributable to:\na) Owners of the Company 69,621 66,702",
            "Net Profit Attributable to: a) Owners of the Company",
        ),
        ("9,80,136", None),
        (None, None),
    ],
)
def test_a_filing_s_own_label_is_its_quote_up_to_the_first_number(
    quote: str | None, label: str | None
) -> None:
    assert reported_label(quote) == label


def test_each_figure_and_its_rival_keep_what_their_source_calls_them() -> None:
    labelled = [
        replace(stored, reported_as="Revenue from Operations")
        if stored.citation.source == "filing"
        else replace(stored, reported_as="Sales")
        for stored in TWO_SOURCES
    ]
    [only] = measure_facts(labelled, REVENUE, periods=("FY2024",))
    assert only.reported_as == "Revenue from Operations"
    assert [rival.reported_as for rival in only.rivals] == ["Sales"]


def test_a_change_carries_its_difference() -> None:
    [latest, _] = change_views(measure_facts(TWO_SOURCES, REVENUE))
    assert latest.difference == Decimal("93")
