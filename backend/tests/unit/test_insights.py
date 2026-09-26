"""What the stock page shows, computed from stored rows (P11c): pure functions, no database.

Key facts: the winning figure for each metric and fiscal year (app/derived.py's choose_fact), in
one view per metric (consolidated ₹ first), the latest three years, each with its citation.
Derived values and sentiment are computed on read (ADR 009) and cite the facts they used.
"""

from datetime import date
from decimal import Decimal

from app.derived import EventRow, FactRow
from app.insights import (
    Citation,
    StoredEvent,
    StoredFact,
    derived_views,
    filing_citation,
    key_facts,
    recent_events,
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
