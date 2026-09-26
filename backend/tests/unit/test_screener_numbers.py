"""The numbers on a stock's screener.in company page, read deterministically (P11).

HTML in, cited rows out: every figure says which section, row and column of the page it came from,
and the mapping to our metric names is a fixed table. No LLM. The pages are synthetic (DemoCo), with
the real page's structure and invented numbers.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.screener_numbers import (
    MappedFact,
    ScreenerFigure,
    TopRatios,
    map_to_vocabulary,
    parse_fundamentals,
    parse_top_ratios,
)
from tests.screener_html import DEMOBANK_PAGE, DEMOCO_PAGE


def _figure(section: str, row: str, column: str, value: str | None) -> ScreenerFigure:
    return ScreenerFigure(section, row, column, None if value is None else Decimal(value))


def _cell(figures: list[ScreenerFigure], section: str, row: str, column: str) -> Decimal | None:
    [match] = [
        f for f in figures if (f.section, f.row_label, f.column_label) == (section, row, column)
    ]
    return match.value


def _facts(facts: list[MappedFact]) -> list[tuple[str, str, Decimal]]:
    return [(f.metric, f.period, f.value) for f in facts]


# --- parse_fundamentals --------------------------------------------------------------------------


def test_every_cell_of_the_five_tables_is_read_with_its_row_and_column() -> None:
    figures = parse_fundamentals(DEMOCO_PAGE)
    assert {f.section for f in figures} == {
        "quarters",
        "profit-loss",
        "balance-sheet",
        "cash-flow",
        "ratios",
    }
    assert _cell(figures, "quarters", "Sales", "Sep 2025") == Decimal("1234")
    assert _cell(figures, "profit-loss", "EPS in Rs", "Mar 2026") == Decimal("16.24")
    assert _cell(figures, "balance-sheet", "Reserves", "Mar 2025") == Decimal("3450")
    assert _cell(figures, "cash-flow", "Cash from Investing Activity", "Mar 2025") == Decimal(
        "-1240"
    )
    assert _cell(figures, "ratios", "Debtor Days", "Mar 2024") == Decimal("70")


def test_the_first_rows_come_out_in_page_order() -> None:
    figures = parse_fundamentals(DEMOCO_PAGE)
    assert figures[:5] == [
        _figure("quarters", "Sales", "Sep 2025", "1234"),
        _figure("quarters", "Sales", "Dec 2025", "1310"),
        _figure("quarters", "Sales", "Mar 2026", "1402"),
        _figure("quarters", "Sales", "Jun 2026", "1455"),
        _figure("quarters", "Expenses", "Sep 2025", "1010"),
    ]


def test_expander_plus_and_non_breaking_space_are_cleaned_from_row_labels() -> None:
    labels = {f.row_label for f in parse_fundamentals(DEMOCO_PAGE)}
    assert {"Sales", "Net Profit", "Borrowings", "Other Income", "EPS in Rs"} <= labels
    assert not any(label.endswith("+") or "\xa0" in label for label in labels)


def test_negatives_percentages_and_the_ttm_column_are_read() -> None:
    figures = parse_fundamentals(DEMOCO_PAGE)
    assert _cell(figures, "quarters", "Other Income", "Sep 2025") == Decimal("-12")
    assert _cell(figures, "quarters", "OPM %", "Mar 2026") == Decimal("19")
    assert _cell(figures, "ratios", "ROCE %", "Mar 2026") == Decimal("32")
    assert _cell(figures, "profit-loss", "Sales", "TTM") == Decimal("5402")


def test_an_empty_cell_is_none_not_zero() -> None:
    figures = parse_fundamentals(DEMOCO_PAGE)
    assert _cell(figures, "quarters", "Net Profit", "Mar 2026") is None
    assert _cell(figures, "quarters", "Other Income", "Mar 2026") == Decimal("0")
    assert _cell(figures, "ratios", "Inventory Days", "Mar 2025") is None


def test_the_raw_pdf_row_is_not_read() -> None:
    assert not any(f.row_label == "Raw PDF" for f in parse_fundamentals(DEMOCO_PAGE))


def test_tables_outside_the_five_sections_and_non_data_tables_are_ignored() -> None:
    figures = parse_fundamentals(DEMOCO_PAGE)
    labels = {f.row_label for f in figures}
    assert "Promoters" not in labels  # shareholding section
    assert "No. of Shareholders" not in labels
    assert "3 Years:" not in labels  # the growth "ranges-table" inside profit-loss


def test_indian_and_western_digit_grouping_both_read() -> None:
    page = DEMOCO_PAGE.replace("2,950", "2,95,000").replace("1,020", "1,020,000")
    figures = parse_fundamentals(page)
    assert _cell(figures, "balance-sheet", "Reserves", "Mar 2024") == Decimal("295000")
    assert _cell(figures, "balance-sheet", "Borrowings", "Mar 2025") == Decimal("1020000")


@pytest.mark.parametrize("junk", ["n/a", "1.2.3", "NaN", "Infinity", "--", "12%%", "1e5"])
def test_a_cell_that_is_not_a_plain_number_is_none(junk: str) -> None:
    page = DEMOCO_PAGE.replace("1,234", junk)
    assert _cell(parse_fundamentals(page), "quarters", "Sales", "Sep 2025") is None


def test_a_money_table_without_the_crores_caption_is_not_read() -> None:
    """The unit is stated once per table, in its caption. If that ever changes, read nothing from
    the table rather than guess the unit."""
    page = DEMOCO_PAGE.replace(
        'Figures in Rs. Crores\n            /\n            <a href="/company/DEMO/#quarters"',
        'Figures in Rs. Lakhs\n            /\n            <a href="/company/DEMO/#quarters"',
    )
    sections = {f.section for f in parse_fundamentals(page)}
    assert "quarters" not in sections
    assert "profit-loss" in sections


def test_a_row_longer_than_the_header_stops_at_the_last_column() -> None:
    page = DEMOCO_PAGE.replace(
        '<td class="">\n                  1,455\n              </td>',
        '<td class="">\n                  1,455\n              </td><td>99</td>',
    )
    sales = [
        f for f in parse_fundamentals(page) if (f.section, f.row_label) == ("quarters", "Sales")
    ]
    assert [f.column_label for f in sales] == ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]


def test_a_table_nested_inside_a_data_table_is_not_read_as_rows() -> None:
    page = DEMOCO_PAGE.replace(
        '<td class="">\n                  1,234\n              </td>',
        '<td class="">\n                  1,234<table><tr><td>Ghost</td><td>7</td></tr></table>'
        "\n              </td>",
    )
    figures = parse_fundamentals(page)
    assert not any(f.row_label == "Ghost" for f in figures)
    assert _cell(figures, "quarters", "Sales", "Dec 2025") == Decimal("1310")


@pytest.mark.parametrize(
    "html",
    [
        "",
        "not html at all",
        "<html><body><p>Login required</p></body></html>",
        '<section id="quarters"><table class="data-table"><tr><td>Sales',  # cut off
        '<section id="quarters"><table class="data-table"><tbody><tr><td>Sales</td>'
        "<td>12</td></tr></tbody></table></section>",  # no header row: columns unknown
        '<section id="quarters"><table class="data-table"><tr></tr></table></section>',
        '<section id="ratios"><table class="data-table"><tr><th></th><th>Mar 2026</th></tr>'
        "<tr><td></td><td>5</td></tr></table></section>",  # a row without a label
        "<![foo[ x ]]>",  # makes html.parser raise AssertionError
        "</section></table></tr></td>",
    ],
)
def test_malformed_or_missing_html_gives_an_empty_list(html: str) -> None:
    assert parse_fundamentals(html) == []


def test_a_bank_page_has_its_own_row_labels() -> None:
    labels = {(f.section, f.row_label) for f in parse_fundamentals(DEMOBANK_PAGE)}
    assert ("quarters", "Revenue") in labels
    assert ("quarters", "Financing Profit") in labels
    assert ("balance-sheet", "Deposits") in labels
    assert ("balance-sheet", "Borrowing") in labels
    assert ("ratios", "ROE %") in labels


# --- parse_top_ratios ----------------------------------------------------------------------------


def test_top_ratios_are_read_with_rupee_signs_crores_and_percent_stripped() -> None:
    assert parse_top_ratios(DEMOCO_PAGE) == TopRatios(
        market_cap_crore=Decimal("123456"),
        current_price=Decimal("3100"),
        stock_pe=Decimal("22.5"),
        book_value=Decimal("610"),
        dividend_yield_percent=Decimal("1.80"),
        roce_percent=Decimal("32.0"),
        roe_percent=Decimal("25.5"),
        face_value=Decimal("1.00"),
    )


def test_a_missing_or_blank_top_ratio_is_none() -> None:
    ratios = parse_top_ratios(DEMOBANK_PAGE)
    assert ratios.dividend_yield_percent is None  # no such item on the page
    assert ratios.stock_pe is None  # the item is there, its number is blank
    assert ratios.market_cap_crore == Decimal("98765")
    assert ratios.roe_percent == Decimal("15.2")


def test_high_low_is_a_pair_and_is_ignored() -> None:
    ratios = parse_top_ratios(DEMOCO_PAGE)
    assert Decimal("3500") not in vars(ratios).values()
    assert Decimal("2700") not in vars(ratios).values()


def test_a_market_cap_not_stated_in_crores_is_not_read() -> None:
    page = DEMOCO_PAGE.replace("1,23,456</span> Cr.", "1,23,456</span> Lakh")
    assert parse_top_ratios(page).market_cap_crore is None


def test_a_ratio_with_two_numbers_is_not_read() -> None:
    page = DEMOCO_PAGE.replace(
        '<span class="number">610</span>',
        '<span class="number">610</span><span class="number">1</span>',
    )
    assert parse_top_ratios(page).book_value is None


@pytest.mark.parametrize("html", ["", "<ul id='top-ratios'><li><span class='name'>ROE", "<![x["])
def test_top_ratios_of_a_malformed_page_are_all_none(html: str) -> None:
    assert parse_top_ratios(html) == TopRatios(None, None, None, None, None, None, None, None)


def test_top_ratios_outside_the_list_are_not_read() -> None:
    page = '<div><span class="name">ROE</span><span class="number">99</span></div>'
    assert parse_top_ratios(page).roe_percent is None


def test_markup_between_the_list_items_is_ignored() -> None:
    page = (
        '<ul id="top-ratios"><span class="number">7</span><p>note</p>'
        '<li><span class="name">ROE</span><span class="value"><span class="number">12.5</span>'
        " %</span></li></ul>"
    )
    assert parse_top_ratios(page).roe_percent == Decimal("12.5")


# --- map_to_vocabulary: an ordinary company ------------------------------------------------------


def _democo_facts() -> list[MappedFact]:
    return map_to_vocabulary(
        parse_fundamentals(DEMOCO_PAGE), is_financial=False, view="consolidated"
    )


def test_quarterly_rows_map_to_indian_fiscal_quarters() -> None:
    facts = [f for f in _democo_facts() if f.section == "quarters"]
    assert _facts(facts) == [
        ("revenue_from_operations", "Q2FY2026", Decimal("1234")),
        ("revenue_from_operations", "Q3FY2026", Decimal("1310")),
        ("revenue_from_operations", "Q4FY2026", Decimal("1402")),
        ("revenue_from_operations", "Q1FY2027", Decimal("1455")),
        ("net_profit", "Q2FY2026", Decimal("210")),
        ("net_profit", "Q3FY2026", Decimal("225")),
        # Mar 2026 is blank on the page: no fact, not a zero
        ("net_profit", "Q1FY2027", Decimal("250")),
        ("eps_basic", "Q2FY2026", Decimal("4.20")),
        ("eps_basic", "Q3FY2026", Decimal("4.50")),
        ("eps_basic", "Q4FY2026", Decimal("4.90")),
        ("eps_basic", "Q1FY2027", Decimal("5.00")),
    ]


def test_quarter_ends_are_the_last_day_of_the_month() -> None:
    ends = {f.period: f.period_end for f in _democo_facts() if f.section == "quarters"}
    assert ends == {
        "Q2FY2026": date(2025, 9, 30),
        "Q3FY2026": date(2025, 12, 31),
        "Q4FY2026": date(2026, 3, 31),
        "Q1FY2027": date(2026, 6, 30),
    }


def test_annual_rows_map_to_fiscal_years_and_ttm_is_skipped() -> None:
    facts = [f for f in _democo_facts() if f.section == "profit-loss"]
    assert _facts(facts) == [
        ("revenue_from_operations", "FY2024", Decimal("4100")),
        ("revenue_from_operations", "FY2025", Decimal("4650")),
        ("revenue_from_operations", "FY2026", Decimal("5120")),
        ("net_profit", "FY2024", Decimal("610")),
        ("net_profit", "FY2025", Decimal("700")),
        ("net_profit", "FY2026", Decimal("812")),
        ("eps_basic", "FY2024", Decimal("12.20")),
        ("eps_basic", "FY2025", Decimal("14.00")),
        ("eps_basic", "FY2026", Decimal("16.24")),
    ]
    assert {f.period_end for f in facts} == {
        date(2024, 3, 31),
        date(2025, 3, 31),
        date(2026, 3, 31),
    }


def test_balance_sheet_borrowings_and_equity_as_capital_plus_reserves() -> None:
    facts = [f for f in _democo_facts() if f.section == "balance-sheet"]
    assert _facts(facts) == [
        ("total_borrowings", "FY2024", Decimal("820")),
        ("total_borrowings", "FY2025", Decimal("1020")),
        ("total_borrowings", "FY2026", Decimal("1250")),
        # Sep 2026 (a half-year balance sheet) is not a fiscal year end: skipped, not guessed
        ("total_equity", "FY2024", Decimal("3000")),
        ("total_equity", "FY2025", Decimal("3500")),
        # Mar 2026: Reserves is blank, so there is no equity for that year
    ]


def test_the_equity_fact_cites_both_rows() -> None:
    [equity] = [f for f in _democo_facts() if f.metric == "total_equity" and f.period == "FY2024"]
    assert equity == MappedFact(
        metric="total_equity",
        period="FY2024",
        period_end=date(2024, 3, 31),
        basis="consolidated",
        value=Decimal("3000"),
        unit="INR_CRORE",
        currency="INR",
        section="balance-sheet",
        row_label="Equity Capital + Reserves",
        column_label="Mar 2024",
    )


def test_equity_needs_the_capital_row_too() -> None:
    figures = [_figure("balance-sheet", "Reserves", "Mar 2026", "100")]
    assert map_to_vocabulary(figures, is_financial=False, view="consolidated") == []


def test_units_currency_and_citation_of_each_kind_of_fact() -> None:
    by_metric = {f.metric: f for f in _democo_facts() if f.period == "FY2025"}
    assert (
        by_metric["revenue_from_operations"].unit,
        by_metric["revenue_from_operations"].currency,
    ) == (
        "INR_CRORE",
        "INR",
    )
    assert (by_metric["eps_basic"].unit, by_metric["eps_basic"].currency) == (
        "INR_PER_SHARE",
        "INR",
    )
    revenue = by_metric["revenue_from_operations"]
    assert (revenue.section, revenue.row_label, revenue.column_label) == (
        "profit-loss",
        "Sales",
        "Mar 2025",
    )
    assert revenue.basis == "consolidated"


def test_roce_is_never_mapped_to_return_on_equity() -> None:
    assert not any(f.metric == "return_on_equity" for f in _democo_facts())


def test_an_roe_row_maps_to_return_on_equity_as_a_percent() -> None:
    figures = [_figure("ratios", "ROE %", "Mar 2026", "24")]
    [fact] = map_to_vocabulary(figures, is_financial=False, view="standalone")
    assert (fact.metric, fact.period, fact.value, fact.unit, fact.currency, fact.basis) == (
        "return_on_equity",
        "FY2026",
        Decimal("24"),
        "PERCENT",
        None,
        "standalone",
    )


def test_revenue_row_label_also_maps_for_an_ordinary_company() -> None:
    figures = [_figure("profit-loss", "Revenue", "Mar 2026", "900")]
    [fact] = map_to_vocabulary(figures, is_financial=False, view="consolidated")
    assert fact.metric == "revenue_from_operations"


def test_unmapped_sections_and_rows_give_nothing() -> None:
    figures = [
        _figure("cash-flow", "Net Cash Flow", "Mar 2026", "10"),
        _figure("quarters", "OPM %", "Jun 2026", "20"),
        _figure("ratios", "ROCE %", "Mar 2026", "30"),
        _figure("peers", "Net Profit", "Mar 2026", "40"),
    ]
    assert map_to_vocabulary(figures, is_financial=False, view="consolidated") == []


@pytest.mark.parametrize(
    ("section", "column"),
    [
        ("profit-loss", "Dec 2019"),  # an old year end in another month: not guessed
        ("profit-loss", "TTM"),
        ("profit-loss", "2026"),
        ("quarters", "Aug 2025"),  # not a quarter end
        ("quarters", "Sept 2025"),
        ("quarters", "TTM"),
        ("balance-sheet", "Sep 2026"),
    ],
)
def test_columns_that_are_not_a_fiscal_period_are_skipped(section: str, column: str) -> None:
    figures = [
        _figure(section, "Net Profit", column, "5"),
        _figure(section, "Borrowings", column, "5"),
        _figure("balance-sheet", "Equity Capital", column, "5"),
        _figure("balance-sheet", "Reserves", column, "5"),
    ]
    assert map_to_vocabulary(figures, is_financial=False, view="consolidated") == []


# --- map_to_vocabulary: a bank -------------------------------------------------------------------


def _bank_facts() -> list[MappedFact]:
    return map_to_vocabulary(
        parse_fundamentals(DEMOBANK_PAGE), is_financial=True, view="consolidated"
    )


def test_a_bank_maps_profit_eps_equity_and_roe_only() -> None:
    assert _facts(_bank_facts()) == [
        ("net_profit", "Q3FY2026", Decimal("850")),
        ("net_profit", "Q4FY2026", Decimal("910")),
        ("net_profit", "Q1FY2027", Decimal("960")),
        ("eps_basic", "Q3FY2026", Decimal("11.30")),
        ("eps_basic", "Q4FY2026", Decimal("12.10")),
        ("eps_basic", "Q1FY2027", Decimal("12.75")),
        ("net_profit", "FY2025", Decimal("3210")),
        ("net_profit", "FY2026", Decimal("3520")),
        ("eps_basic", "FY2025", Decimal("42.70")),
        ("eps_basic", "FY2026", Decimal("46.80")),
        ("return_on_equity", "FY2025", Decimal("14")),
        ("return_on_equity", "FY2026", Decimal("15")),
        ("total_equity", "FY2025", Decimal("25000")),
        ("total_equity", "FY2026", Decimal("28200")),
    ]


def test_a_banks_revenue_and_borrowings_are_never_mapped() -> None:
    figures = [
        _figure("profit-loss", "Revenue", "Mar 2026", "1"),
        _figure("profit-loss", "Sales", "Mar 2026", "1"),
        _figure("balance-sheet", "Borrowing", "Mar 2026", "1"),
        _figure("balance-sheet", "Borrowings", "Mar 2026", "1"),
        _figure("balance-sheet", "Deposits", "Mar 2026", "1"),
    ]
    assert map_to_vocabulary(figures, is_financial=True, view="consolidated") == []


def test_mapping_an_empty_list_gives_nothing() -> None:
    assert map_to_vocabulary([], is_financial=True, view="consolidated") == []
