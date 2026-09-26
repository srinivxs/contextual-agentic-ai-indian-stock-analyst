"""Definition guards (P11, the owner's option A after the hand check): quotes whose figure is a
different number from the one the metric means are refused, by plain code, before any storing.

Found in the hand check of the first real run (all four were real figures on the cited page, but
not the metric's figure): a dividend PAID during the year (for the year before), a FINAL dividend
instead of the year's total, NON-CURRENT borrowings as total borrowings, and a net profit
EXCLUDING exceptional items.
"""

import pytest

from app.fact_validation import definition_problem, label_in_quote


@pytest.mark.parametrize(
    ("metric", "quote", "problem"),
    [
        # net profit must be the reported figure, not an adjusted one
        (
            "net_profit",
            "profit for the year (excluding exceptional items) was ₹5,282 crore",
            "adjusted_figure",
        ),
        ("net_profit", "Net profit before exceptional items ₹ 1,234 crore", "adjusted_figure"),
        ("net_profit", "Adjusted net profit of ₹ 1,234 crore", "adjusted_figure"),
        ("net_profit", "Underlying net profit rose to ₹ 1,234 crore", "adjusted_figure"),
        ("net_profit", "Net profit for the year ₹ 1,234 crore", None),
        # a dividend must be the year's total, not one instalment, and not what was paid
        ("dividend_per_share", "a final dividend of ₹31 per share", "partial_dividend"),
        ("dividend_per_share", "an interim dividend of ₹11 per equity share", "partial_dividend"),
        ("dividend_per_share", "special dividend of ₹66 per share", "partial_dividend"),
        (
            "dividend_per_share",
            "final dividend of ₹31 per share, taking the total dividend for the year to ₹110",
            None,  # the total is named: the validator's number check decides which number
        ),
        (
            "dividend_per_share",
            "Dividend on Equity Shares [Dividend per Share ₹ 5.5 (Previous Year ₹ 10)]",
            "dividend_paid_not_declared",
        ),
        ("dividend_per_share", "Dividends paid ₹ 5.5 per share", "dividend_paid_not_declared"),
        ("dividend_per_share", "Dividend Per Share ₹ 110.00 126.00 73.00", None),
        ("dividend_per_share", "total dividend per equity share for 2025-26 is ₹15.50", None),
    ],
)
def test_a_figure_of_another_definition_is_refused(
    metric: str, quote: str, problem: str | None
) -> None:
    assert definition_problem(metric, quote) == problem


def test_other_metrics_have_no_definition_guard() -> None:
    assert definition_problem("eps_basic", "EPS excluding exceptional items ₹ 12") is None


@pytest.mark.parametrize(
    ("quote", "names_it"),
    [
        ("Total borrowings ₹ 2,000 crore", True),
        ("Total debt stood at ₹ 2,000 crore", True),
        ("Gross debt as on March 31, 2026 was ₹ 2,000 crore", True),
        ("Borrowings 16 2,36,899 2,22,712", False),  # a balance-sheet line: non-current only
        ("Net debt of ₹ 100 crore", False),
    ],
)
def test_total_borrowings_must_be_named_as_a_total(quote: str, names_it: bool) -> None:
    assert label_in_quote("total_borrowings", quote) is names_it


def test_the_validator_refuses_a_figure_of_another_definition() -> None:
    from app.fact_validation import Candidate, Rejection, validate

    quote = "Net profit (excluding exceptional items) for FY2026 was ₹ 5,282 crore"
    result = validate(
        Candidate(
            metric="net_profit",
            value_text="5,282",
            unit_word="crore",
            currency="INR",
            period_text="FY2026",
            basis="consolidated",
            page=1,
            quote=quote,
        ),
        pages={1: f"DemoCo Limited. {quote}. Consolidated."},
        is_financial=False,
        document_month=None,
    )
    assert result == Rejection(code="adjusted_figure", metric="net_profit", page=1)
