"""The chat's year-by-year table (redesign): built by code from screener.in's stored series.

All figures are synthetic (DemoCo-style numbers on the seeded symbols are used only as labels).
"""

from decimal import Decimal

from app.chat.tables import build_table
from app.chat.understand import Question
from app.series import SeriesPoint

URL = "https://www.screener.in/company/TCS/consolidated/"
MINUS = chr(0x2212)


def question(*metrics: str, symbols: tuple[str, ...] = ("TCS",)) -> Question:
    return Question(
        symbols=symbols,
        metrics=metrics,
        wants_growth=False,
        wants_events=False,
        periods=(),
        from_history=False,
    )


def point(year: int, value: str, url: str = URL) -> SeriesPoint:
    return SeriesPoint(
        period=f"FY{year}",
        value=Decimal(value),
        source_url=url,
        source_section="profit-loss",
        source_row="Net Profit",
        source_column=f"Mar {year}",
    )


def series(*pairs: tuple[int, str]) -> list[SeriesPoint]:
    """Oldest first, like load_series."""
    return [point(year, value) for year, value in pairs]


def test_rows_are_newest_first_with_indian_grouping_and_the_change_on_the_year_before() -> None:
    points = series((2024, "12000000"), (2025, "12345678"), (2026, "12500000"))

    table = build_table(question("net_profit"), {"net_profit": points})

    assert table is not None
    assert table.title == "TCS net profit (consolidated, ₹ crore)"
    assert table.columns == ("Year", "Net profit (₹ crore)", "Change")
    assert table.rows == (
        ("FY2026", "1,25,00,000", "+1.3%"),
        ("FY2025", "1,23,45,678", "+2.9%"),
        ("FY2024", "1,20,00,000", ""),
    )
    assert table.source_label == "screener.in · consolidated, full years"
    assert table.source_url == URL


def test_at_most_five_years_and_the_oldest_shown_still_has_its_change() -> None:
    points = series(
        (2020, "100"), (2021, "110"), (2022, "121"), (2023, "121"), (2024, "100"), (2025, "50"),
        (2026, "51"),
    )  # fmt: skip

    table = build_table(question("net_profit"), {"net_profit": points})

    assert table is not None
    assert [row[0] for row in table.rows] == ["FY2026", "FY2025", "FY2024", "FY2023", "FY2022"]
    assert [row[2] for row in table.rows] == [
        "+2.0%",
        f"{MINUS}50.0%",
        f"{MINUS}17.4%",
        "0.0%",
        "+10.0%",
    ]


def test_the_oldest_row_shown_has_no_change_when_its_prior_year_is_not_stored() -> None:
    table = build_table(
        question("net_profit"), {"net_profit": series((2025, "200"), (2026, "210"))}
    )

    assert table is not None
    assert table.rows == (("FY2026", "210", "+5.0%"), ("FY2025", "200", ""))


def test_a_gap_in_the_years_gives_no_change_for_the_year_after_it() -> None:
    table = build_table(
        question("net_profit"), {"net_profit": series((2023, "100"), (2026, "150"))}
    )

    assert table is not None
    assert table.rows == (("FY2026", "150", ""), ("FY2023", "100", ""))


def test_a_fall_uses_a_real_minus_sign_and_one_decimal() -> None:
    table = build_table(
        question("net_profit"), {"net_profit": series((2025, "1000"), (2026, "978.95"))}
    )

    assert table is not None
    assert table.rows[0] == ("FY2026", "978.95", f"{MINUS}2.1%")


def test_rounding_is_half_up_and_a_tiny_fall_shows_no_negative_zero() -> None:
    up = build_table(question("net_profit"), {"net_profit": series((2025, "2000"), (2026, "2001"))})
    tiny = build_table(
        question("net_profit"), {"net_profit": series((2025, "100000"), (2026, "99999"))}
    )

    assert up is not None
    assert up.rows[0][2] == "+0.1%"  # 0.05 rounds up
    assert tiny is not None
    assert tiny.rows[0][2] == "0.0%"


def test_a_loss_year_is_shown_with_its_sign_and_the_change_is_against_its_size() -> None:
    table = build_table(
        question("net_profit"), {"net_profit": series((2025, "-100"), (2026, "-50"))}
    )

    assert table is not None
    assert table.rows[0] == ("FY2026", "-50", "+50.0%")


def test_a_zero_prior_year_gives_no_change() -> None:
    table = build_table(question("net_profit"), {"net_profit": series((2025, "0"), (2026, "50"))})

    assert table is not None
    assert table.rows[0][2] == ""


def test_fewer_than_two_years_gives_no_table() -> None:
    assert build_table(question("net_profit"), {"net_profit": series((2026, "100"))}) is None
    assert build_table(question("net_profit"), {"net_profit": []}) is None
    assert build_table(question("net_profit"), {}) is None


def test_revenue_uses_its_own_label_and_title() -> None:
    points = series((2025, "1000"), (2026, "1100"))

    table = build_table(question("revenue_from_operations"), {"revenue_from_operations": points})

    assert table is not None
    assert table.title == "TCS revenue from operations (consolidated, ₹ crore)"
    assert table.columns[1] == "Revenue from operations (₹ crore)"


def test_a_bank_asked_about_revenue_gets_the_measure_that_has_figures() -> None:
    asked = question("revenue_from_operations", "net_interest_income", symbols=("HDFCBANK",))
    only_nii = {
        "revenue_from_operations": [],
        "net_interest_income": series((2025, "1000"), (2026, "1100")),
    }
    only_revenue = {
        "revenue_from_operations": series((2025, "1000"), (2026, "1100")),
        "net_interest_income": series((2026, "5")),
    }

    from_nii = build_table(asked, only_nii)
    from_revenue = build_table(asked, only_revenue)

    assert from_nii is not None
    assert from_nii.title == "HDFCBANK net interest income (consolidated, ₹ crore)"
    assert from_nii.columns[1] == "Net interest income (₹ crore)"
    assert from_revenue is not None
    assert from_revenue.title.startswith("HDFCBANK revenue from operations")


def test_two_stocks_or_no_stock_named_give_no_table() -> None:
    points = {"net_profit": series((2025, "100"), (2026, "110"))}

    assert build_table(question("net_profit", symbols=("TCS", "RELIANCE")), points) is None
    assert build_table(question("net_profit", symbols=()), points) is None


def test_a_question_about_another_metric_gives_no_table() -> None:
    points = {"net_profit": series((2025, "100"), (2026, "110"))}

    assert build_table(question("total_borrowings"), points) is None
    assert build_table(question(), points) is None


def test_profit_and_revenue_together_is_ambiguous_so_no_table() -> None:
    points = {
        "net_profit": series((2025, "100"), (2026, "110")),
        "revenue_from_operations": series((2025, "1000"), (2026, "1100")),
    }

    assert build_table(question("revenue_from_operations", "net_profit"), points) is None


def test_the_table_cites_the_newest_figures_page() -> None:
    points = [point(2025, "100", "https://www.screener.in/old/"), point(2026, "110", URL)]

    table = build_table(question("net_profit"), {"net_profit": points})

    assert table is not None
    assert table.source_url == URL
