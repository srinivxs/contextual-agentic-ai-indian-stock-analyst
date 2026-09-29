"""A question asking only for stored figures is answered by code (the owner's review,
2026-09-29): the figure, its period, basis and source, with no LLM call. Synthetic figures."""

from dataclasses import replace
from decimal import Decimal

from app.chat.answer_check import check_answer
from app.chat.code_answers import missing_lines
from app.chat.evidence import EvidenceItem, Figure
from app.chat.lookup import lookup_claims
from app.chat.understand import understand


def fact(
    item_id: str,
    symbol: str,
    metric: str,
    period: str,
    amount: str,
    value: str,
    *,
    label: str = "Annual report · Annual Report 2024 · p.9",
) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        kind="fact",
        symbol=symbol,
        text=f"{symbol} · {metric} · {period} · consolidated · {amount} · {label.split(' · ')[0]}",
        source="filing",
        label=label,
        url=None,
        quote=None,
        metric=metric,
        period=period,
        figures=(Figure(symbol, metric, period, "consolidated", Decimal(value)),),
        amount=amount,
    )


EVIDENCE = [
    fact("F1", "RELIANCE", "revenue_from_operations", "FY2025", "₹1,100 crore", "1100"),
    fact("F2", "RELIANCE", "revenue_from_operations", "FY2024", "₹1,000 crore", "1000"),
    fact(
        "F3",
        "TCS",
        "net_profit",
        "FY2025",
        "₹200 crore",
        "200",
        label="screener.in · profit-loss · Net Profit · Mar 2025",
    ),
    fact("F4", "HDFCBANK", "net_interest_income", "FY2025", "₹300 crore", "300"),
]


def claims_for(question: str) -> list[tuple[str, tuple[str, ...]]] | None:
    claims = lookup_claims(understand(question, history=[]), EVIDENCE)
    return None if claims is None else [(c.text, c.citations) for c in claims]


def test_a_named_year_is_answered_from_its_stored_figure() -> None:
    assert claims_for("What was Reliance's revenue in FY2024?") == [
        (
            "Reliance's revenue from operations for FY2024 was ₹1,000 crore "
            "(consolidated; annual report).",
            ("F2",),
        )
    ]


def test_with_no_year_named_the_latest_is_given_and_the_source_is_named() -> None:
    assert claims_for("What was TCS's net profit, and what source supports the figure?") == [
        ("TCS's net profit for FY2025 was ₹200 crore (consolidated; screener.in).", ("F3",))
    ]


def test_a_bank_s_revenue_is_never_its_net_interest_income() -> None:
    # no revenue figure for the bank: code states nothing, and says it is not available
    assert claims_for("What is HDFC Bank's revenue?") == []
    question = understand("What is HDFC Bank's revenue?", history=[])
    assert missing_lines(question, EVIDENCE) == [
        "HDFC Bank's revenue from operations: not available in the current data."
    ]
    # asked for by name, it is given
    assert claims_for("What is HDFC Bank's net interest income?") == [
        (
            "HDFC Bank's net interest income for FY2025 was ₹300 crore "
            "(consolidated; annual report).",
            ("F4",),
        )
    ]


def test_what_is_there_is_stated_and_what_is_not_is_named() -> None:
    question = understand("What was the net profit of TCS and HDFC Bank in FY2025?", history=[])
    claims = lookup_claims(question, EVIDENCE)
    assert claims is not None
    assert [c.citations for c in claims] == [("F3",)]
    assert missing_lines(question, EVIDENCE) == [
        "HDFC Bank's net profit for FY2025: not available in the current data."
    ]


def test_several_years_and_stocks_each_get_their_own_claim() -> None:
    claims = claims_for("What was Reliance's revenue in FY2024 and FY2025?")
    assert claims is not None
    assert [citations for _, citations in claims] == [("F2",), ("F1",)]


def test_the_code_s_claims_pass_the_checker_like_any_answer() -> None:
    question = understand("What was Reliance's revenue in FY2024?", history=[])
    claims = lookup_claims(question, EVIDENCE)
    assert claims is not None
    assert check_answer(claims, EVIDENCE) == []


def test_a_missing_year_or_an_unnamed_stock_is_not_answered_by_code() -> None:
    assert claims_for("What was Reliance's revenue in FY2019?") is None  # the model looks further
    assert claims_for("What is the latest revenue?") is None  # no stock named: all three searched
    assert claims_for("Why did Reliance's revenue change in FY2025?") is None  # not a lookup


def test_another_source_s_figure_is_never_the_one_given() -> None:
    main = replace(EVIDENCE[1], rivals=("F9",))
    figure = Figure("RELIANCE", "revenue_from_operations", "FY2024", "consolidated", Decimal(990))
    rival = replace(main, id="F9", figures=(figure,), amount="₹990 crore", rivals=(), rival_of="F2")
    claims = lookup_claims(
        understand("What was Reliance's revenue in FY2024?", history=[]),
        [rival, EVIDENCE[0], main, *EVIDENCE[2:]],
    )
    assert claims is not None
    assert [c.citations for c in claims] == [("F2",)]
