"""Matching a profile to stocks with plain rules (P14): pure functions, no database.

Three synthetic stocks (never tied to a real company) cover the interesting shapes:

    DEMO      DemoCo     low debt, pays a dividend, slow growth, ROE 12%
    DEMOBANK  DemoBank   a bank: no debt to equity, no dividend, fast growth, ROE 16%
    DEMOGROW  DemoGrow   high debt, fast growth, no dividend, ROE 22%, negative news
    DEMOBARE  DemoBare   almost no data

The golden tests pin each profile's status per stock; the rest test one rule edge at a time.
DEMO has a steady rising synthetic price, DEMOBANK a falling and jumpy one; DEMOGROW and DEMOBARE
have no prices at all (ADR 025: their momentum falls back to earnings).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.derived import EventRow, FactRow
from app.insights import StoredEvent, StoredFact, filing_citation
from app.insights_store import StockRows
from app.matching.model import Reason, StockMatch
from app.matching.rules import match_all, match_stock
from app.memory.vocabulary import Field, StoredPreference
from app.prices.model import DailyPrice

TODAY = date(2026, 9, 29)
BSE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/demo.pdf"


def fact(
    fact_id: int,
    metric: str,
    period: str,
    value: str,
    *,
    unit: str = "INR_CRORE",
    basis: str = "consolidated",
    currency: str | None = "INR",
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
            source="annual_report",
            source_date=date(year, 6, 30),
        ),
        citation=filing_citation(
            "annual_report", f"Annual Report {year}", "Demo AR", BSE, fact_id, f"{metric} {value}"
        ),
    )


def pair(start_id: int, metric: str, before: str, after: str, **kw: str) -> list[StoredFact]:
    """The same metric for FY2025 and FY2026 (ids start_id and start_id + 1)."""
    return [
        fact(start_id, metric, "FY2025", before, **kw),
        fact(start_id + 1, metric, "FY2026", after, **kw),
    ]


def debt(borrowings: str, equity: str) -> list[StoredFact]:
    return [
        fact(1, "total_borrowings", "FY2026", borrowings),
        fact(2, "total_equity", "FY2026", equity),
    ]


def dividend(value: str) -> StoredFact:
    return fact(5, "dividend_per_share", "FY2026", value, unit="INR_PER_SHARE")


def roe(value: str) -> StoredFact:
    return fact(6, "return_on_equity", "FY2026", value, unit="PERCENT")


def event(event_id: int, day: date, sentiment: str, impact: str = "high") -> StoredEvent:
    return StoredEvent(
        row=EventRow(event_id, "guidance", sentiment, impact, day),  # type: ignore[arg-type]
        summary=f"event {event_id}",
        citation=filing_citation(
            "announcement", "Sep 2026", "Demo news", BSE, event_id, f"quote {event_id}"
        ),
    )


LAST_DAY = date(2026, 9, 28)  # the newest synthetic price file


def prices(
    first: str,
    last: str,
    *,
    days: int = 400,
    zigzag: str = "0",
    end: date = LAST_DAY,
) -> list[DailyPrice]:
    """One synthetic row per calendar day, closes rising in a straight line from first to last;
    a zigzag alternates each close up and down by that fraction. prev_close is the day before's
    close, so there is no corporate action."""
    low, high, wiggle = Decimal(first), Decimal(last), Decimal(zigzag)
    closes = []
    for index in range(days):
        line = low + (high - low) * index / (days - 1)
        closes.append((line * (1 + wiggle * (1 if index % 2 else -1))).quantize(Decimal("0.01")))
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            DailyPrice(
                bse_code="500999",
                trade_date=end - timedelta(days=days - 1 - index),
                open=close,
                high=close,
                low=close,
                close=close,
                prev_close=closes[index - 1] if index else close,
                volume=1000,
            )
        )
    return rows


def eps(value: str, period: str = "FY2026") -> StoredFact:
    return fact(8, "eps_basic", period, value, unit="INR_PER_SHARE")


def stock(
    symbol: str,
    facts: list[StoredFact],
    *,
    financial: bool = False,
    events: list[StoredEvent] | None = None,
    share_prices: list[DailyPrice] | None = None,
) -> StockRows:
    return StockRows(
        1, symbol, f"Name {symbol}", financial, facts, events or [], share_prices or []
    )


def profile(**fields: str | tuple[str, ...]) -> list[StoredPreference]:
    """profile(risk_preference="conservative", investment_style=("income", "growth"))"""
    return [
        StoredPreference(
            field=name,  # type: ignore[arg-type]
            values=(values,) if isinstance(values, str) else values,
            quote="q",
            source="chat",
            updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        for name, values in fields.items()
    ]


def only(match: StockMatch, criterion: str) -> Reason:
    (reason,) = [r for r in match.reasons if r.criterion == criterion]
    return reason


def outcomes(match: StockMatch) -> dict[str, str]:
    return {r.criterion: r.outcome for r in match.reasons}


def run(
    prefs: list[StoredPreference],
    *facts: StoredFact,
    financial: bool = False,
    share_prices: list[DailyPrice] | None = None,
) -> StockMatch:
    rows = stock("DEMOX", list(facts), financial=financial, share_prices=share_prices)
    return match_stock(prefs, rows, today=TODAY)


# --- the synthetic stocks -------------------------------------------------------------------------

NEGATIVE_NEWS = [event(i, date(2026, 9, i), "negative") for i in (1, 2, 3, 4)]


def demo_stocks() -> list[StockRows]:
    demo_co = stock(
        "DEMO",
        [
            *debt("20", "100"),
            *pair(3, "revenue_from_operations", "100", "103"),
            *pair(7, "net_profit", "50", "52"),
            fact(20, "net_profit", "FY2024", "48"),  # growth 4.2% then 4%: slowing
            dividend("5"),
            roe("12"),
            eps("8"),  # a close of 120 makes P/E 15
        ],
        share_prices=prices("100", "120"),  # steady, up over six months
    )
    demo_bank = stock(
        "DEMOBANK",
        [
            *pair(3, "net_interest_income", "100", "120"),
            *pair(7, "net_profit", "40", "44"),
            dividend("0"),
            roe("16"),
            eps("5"),  # a close of 150 makes P/E 30
        ],
        financial=True,
        share_prices=prices("200", "150", zigzag="0.015"),  # falling and jumpy
    )
    demo_grow = stock(
        "DEMOGROW",
        [
            *debt("300", "200"),
            *pair(3, "revenue_from_operations", "100", "150"),
            *pair(7, "net_profit", "10", "13"),
            fact(21, "net_profit", "FY2024", "9"),  # growth 11.1% then 30%: speeding up
            roe("22"),
        ],
        events=NEGATIVE_NEWS,
    )
    demo_bare = stock("DEMOBARE", [])
    return [demo_co, demo_bank, demo_grow, demo_bare]


GOLDEN = [
    pytest.param(
        profile(
            risk_preference="conservative",
            debt_preference="avoid_high_debt",
            investment_style="income",
        ),
        ["match", "partial", "no_match", "not_enough_data"],
        id="conservative-avoid-debt-income",
    ),
    pytest.param(
        profile(risk_preference="aggressive", investment_style="growth"),
        ["partial", "match", "match", "not_enough_data"],
        id="aggressive-growth",
    ),
    pytest.param(
        profile(investment_style="quality"),
        ["partial", "match", "match", "not_enough_data"],
        id="quality",
    ),
    pytest.param(
        profile(investment_style=("value", "momentum"), other_preferences="long_term"),
        ["match", "partial", "match", "not_enough_data"],
        id="value-momentum-long-term",
    ),
    pytest.param(
        profile(investment_style="growth", other_preferences="stability"),
        ["partial", "match", "match", "not_enough_data"],
        id="growth-stability",
    ),
    pytest.param([], ["not_enough_data"] * 4, id="empty"),
]


@pytest.mark.parametrize(("prefs", "expected"), GOLDEN)
def test_golden_statuses_per_profile_and_stock(
    prefs: list[StoredPreference], expected: list[str]
) -> None:
    matches = match_all(prefs, demo_stocks(), today=TODAY)
    assert [m.symbol for m in matches] == ["DEMO", "DEMOBANK", "DEMOGROW", "DEMOBARE"]
    assert [m.status for m in matches] == expected


def test_golden_conservative_avoid_debt_income_reasons() -> None:
    prefs = profile(
        risk_preference="conservative", debt_preference="avoid_high_debt", investment_style="income"
    )
    demo, bank, grow, bare = match_all(prefs, demo_stocks(), today=TODAY)
    assert outcomes(demo) == {"debt": "pass", "dividend": "pass", "profit_growth": "pass"}
    assert outcomes(bank) == {
        "debt": "not_assessable",
        "dividend": "miss",
        "profit_growth": "pass",
    }
    assert outcomes(grow) == {"debt": "fail", "profit_growth": "pass"} | {"dividend": "no_data"}
    assert outcomes(bare) == {"debt": "no_data", "dividend": "no_data", "profit_growth": "no_data"}
    assert only(demo, "debt").text == (
        "Debt to equity is 0.2, within the 0.5 a conservative investor prefers."
    )
    assert only(demo, "dividend").text == (
        "The latest dividend is ₹5 per share, so the company pays one. "
        "This judges whether a dividend is paid, not its yield."
    )
    assert (
        only(grow, "debt").text
        == "Debt to equity is 1.5, above the 1.0 limit for avoiding high debt."
    )
    assert (
        only(bank, "debt").text
        == "Debt to equity does not apply to banks: borrowing is their business."
    )
    assert only(grow, "debt").hard is True
    assert [c.label for c in only(demo, "debt").citations] == [
        "Annual report · Annual Report 2026 · p.1",
        "Annual report · Annual Report 2026 · p.2",
    ]


def test_golden_growth_texts_and_bank_label() -> None:
    demo, bank, *_ = match_all(profile(investment_style="growth"), demo_stocks(), today=TODAY)
    assert only(demo, "revenue_growth").text == (
        "Revenue growth is 3%, below the 10% wanted for a growth style."
    )
    assert only(bank, "revenue_growth").text == (
        "Net interest income growth is 20%, at or above the 10% wanted for a growth style."
    )
    assert only(bank, "profit_growth").outcome == "pass"  # exactly 10% passes


def test_golden_quality_text_and_citation() -> None:
    demo, *_ = match_all(profile(investment_style="quality"), demo_stocks(), today=TODAY)
    reason = only(demo, "quality")
    assert reason.text == (
        "Return on equity for FY2026 is 12%, below the 15% wanted for a quality style."
    )
    assert [c.quote for c in reason.citations] == ["return_on_equity 12"]


def test_golden_price_styles_and_a_short_horizon() -> None:
    prefs = profile(investment_style=("value", "momentum"), other_preferences="short_term")
    demo, bank, grow, bare = match_all(prefs, demo_stocks(), today=TODAY)
    assert outcomes(demo) == {"value": "pass", "momentum": "pass", "horizon": "pass"}
    assert outcomes(bank) == {"value": "miss", "momentum": "miss", "horizon": "miss"}
    # no prices at all: value cannot be judged, momentum falls back to earnings, no swings known
    assert outcomes(grow) == {"value": "not_assessable", "momentum": "pass", "horizon": "no_data"}
    assert outcomes(bare) == {
        "value": "not_assessable",
        "momentum": "no_data",
        "horizon": "no_data",
    }
    assert [m.status for m in (demo, bank, grow, bare)] == [
        "match",
        "partial",
        "partial",
        "not_enough_data",
    ]
    assert only(demo, "horizon").preference == "short_term"
    assert only(grow, "value").text == "Value needs share prices, which are not in the data yet."


def test_golden_price_texts_and_citations() -> None:
    demo, bank, *_ = match_all(
        profile(investment_style=("value", "momentum"), other_preferences="short_term"),
        demo_stocks(),
        today=TODAY,
    )
    bse = "BSE daily price file · 28 Sep 2026"
    assert only(demo, "value").text == (
        "Price to earnings is 15, at or below the 20 wanted for a value style. Share price "
        "₹120 (28 Sep 2026) divided by basic EPS of ₹8 for FY2026 (latest full year on record)."
    )
    assert only(demo, "value").citations[0].label == bse
    assert len(only(demo, "value").citations) == 2
    assert only(bank, "value").text.startswith(
        "Price to earnings is 30.5, above the 20 wanted for a value style."
    )
    assert only(demo, "momentum").text.endswith("(to 28 Sep 2026), from BSE's daily price files.")
    assert only(demo, "momentum").text.startswith("Share price is up ")
    assert only(bank, "momentum").text.startswith("Share price is down ")
    assert [c.label for c in only(demo, "momentum").citations] == [bse]
    assert only(demo, "horizon").text.startswith("Share price swings over the last year are ")
    assert "at or below the 30% that suits a short holding." in only(demo, "horizon").text
    assert "above the 30% that suits a short holding." in only(bank, "horizon").text


def test_horizon_long_term_is_named_when_both_are_given() -> None:
    demo, *_ = match_all(
        profile(other_preferences=("short_term", "long_term")), demo_stocks(), today=TODAY
    )
    assert only(demo, "horizon").preference == "long_term"


def test_empty_profile_has_no_reasons_and_no_cautions_even_with_bad_news() -> None:
    grow = match_stock([], demo_stocks()[2], today=TODAY)
    assert (grow.status, grow.reasons, grow.cautions) == ("not_enough_data", (), ())


def test_a_profile_with_no_rule_gives_no_reasons_but_still_cautions() -> None:
    grow = match_stock(profile(debt_preference="debt_ok"), demo_stocks()[2], today=TODAY)
    assert (grow.status, grow.reasons) == ("not_enough_data", ())
    assert len(grow.cautions) == 1


# --- debt -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("borrowings", "equity", "outcome"),
    [("100", "100", "pass"), ("50", "100", "pass"), ("101", "100", "fail")],
)
def test_avoid_high_debt_limit_is_one_and_hard(borrowings: str, equity: str, outcome: str) -> None:
    match = run(profile(debt_preference="avoid_high_debt"), *debt(borrowings, equity))
    reason = only(match, "debt")
    assert (reason.outcome, reason.hard, reason.preference) == (outcome, True, "avoid_high_debt")
    assert match.status == {"pass": "match", "fail": "no_match"}[outcome]


@pytest.mark.parametrize(
    ("borrowings", "equity", "outcome"),
    [("50", "100", "pass"), ("51", "100", "miss"), ("300", "100", "miss")],
)
def test_conservative_alone_limit_is_half_and_soft(
    borrowings: str, equity: str, outcome: str
) -> None:
    match = run(profile(risk_preference="conservative"), *debt(borrowings, equity))
    reason = only(match, "debt")
    assert (reason.outcome, reason.hard, reason.preference) == (outcome, False, "conservative")


@pytest.mark.parametrize(
    ("borrowings", "equity", "outcome", "status"),
    [
        ("50", "100", "pass", "match"),
        ("51", "100", "miss", "partial"),
        ("100", "100", "miss", "partial"),
        ("101", "100", "fail", "no_match"),
    ],
)
def test_both_debt_rules_make_one_reason(
    borrowings: str, equity: str, outcome: str, status: str
) -> None:
    prefs = profile(risk_preference="conservative", debt_preference="avoid_high_debt")
    match = run(prefs, *debt(borrowings, equity), *pair(3, "net_profit", "10", "11"))
    debts = [r for r in match.reasons if r.criterion == "debt"]
    assert len(debts) == 1
    assert (debts[0].outcome, debts[0].hard) == (outcome, True)
    assert match.status == status


def test_both_debt_rules_middle_text_names_both_limits() -> None:
    prefs = profile(risk_preference="conservative", debt_preference="avoid_high_debt")
    match = run(prefs, *debt("80", "100"))
    assert only(match, "debt").text == (
        "Debt to equity is 0.8: within the 1.0 limit but above "
        "the 0.5 a conservative investor prefers."
    )


def test_conservative_alone_miss_text() -> None:
    match = run(profile(risk_preference="conservative"), *debt("80", "100"))
    assert only(match, "debt").text == (
        "Debt to equity is 0.8, above the 0.5 a conservative investor prefers."
    )


def test_avoid_debt_pass_text() -> None:
    match = run(profile(debt_preference="avoid_high_debt"), *debt("80", "100"))
    assert only(match, "debt").text == (
        "Debt to equity is 0.8, within the 1.0 limit for avoiding high debt."
    )


@pytest.mark.parametrize("preference", ["debt_ok", "moderate", "aggressive"])
def test_debt_ok_moderate_and_aggressive_add_no_debt_rule(preference: str) -> None:
    field: Field = "debt_preference" if preference == "debt_ok" else "risk_preference"
    match = run(profile(**{field: preference}), *debt("300", "100"))
    assert "debt" not in [reason.criterion for reason in match.reasons]


# --- the owner's Match fix: aggressive asks for growth, momentum is earnings momentum -------------


def test_aggressive_asks_for_revenue_growth() -> None:
    match = run(
        profile(risk_preference="aggressive"), *pair(1, "revenue_from_operations", "100", "115")
    )
    reason = only(match, "revenue_growth")
    assert (reason.preference, reason.outcome, reason.hard) == ("aggressive", "pass", False)
    assert reason.text == (
        "Revenue growth is 15%, at or above the 10% wanted for an aggressive investor."
    )
    assert match.status == "match"


def test_a_growth_style_and_aggressive_make_one_revenue_reason_named_for_the_style() -> None:
    prefs = profile(risk_preference="aggressive", investment_style="growth")
    match = run(prefs, *pair(1, "revenue_from_operations", "100", "105"))
    assert [r.preference for r in match.reasons if r.criterion == "revenue_growth"] == ["growth"]


def test_earnings_momentum_passes_when_profit_growth_speeds_up() -> None:
    *_, grow, _ = match_all(profile(investment_style="momentum"), demo_stocks(), today=TODAY)
    reason = only(grow, "momentum")
    assert reason.outcome == "pass"
    assert reason.text == (
        "Net profit changed by 30% in FY2026 against 11.1% in FY2025: earnings are speeding "
        "up (earnings momentum, used because six months of share prices are not in the data)."
    )
    assert [c.label for c in reason.citations] == [
        "Annual report · Annual Report 2024 · p.21",
        "Annual report · Annual Report 2025 · p.7",
        "Annual report · Annual Report 2026 · p.8",
    ]
    assert grow.status == "match"


def test_earnings_momentum_misses_when_profit_growth_slows() -> None:
    demo_facts = [
        *pair(7, "net_profit", "50", "52"),
        fact(20, "net_profit", "FY2024", "48"),  # growth 4.2% then 4%: slowing
    ]
    match = run(profile(investment_style="momentum"), *demo_facts)
    reason = only(match, "momentum")
    assert reason.outcome == "miss"
    assert reason.text == (
        "Net profit changed by 4% in FY2026 against 4.2% in FY2025: earnings are not speeding "
        "up (earnings momentum, used because six months of share prices are not in the data)."
    )
    assert match.status == "partial"


def test_momentum_needs_prices_or_three_years_in_a_row() -> None:
    match = run(profile(investment_style="momentum"), *pair(1, "net_profit", "10", "12"))
    reason = only(match, "momentum")
    assert reason.outcome == "no_data"
    assert reason.text == (
        "Momentum needs six months of share prices or three years of net profit in a row "
        "from one source, which are not in the data."
    )
    assert match.status == "not_enough_data"


def test_earnings_momentum_from_a_loss_year_cannot_be_judged() -> None:
    facts = [*pair(1, "net_profit", "-5", "12"), fact(3, "net_profit", "FY2024", "8")]
    reason = only(run(profile(investment_style="momentum"), *facts), "momentum")
    assert reason.outcome == "no_data"
    assert "zero or negative" in reason.text


def test_a_bank_is_not_assessable_for_debt_and_not_a_fail() -> None:
    match = run(profile(debt_preference="avoid_high_debt"), financial=True)
    reason = only(match, "debt")
    assert (reason.outcome, reason.hard) == ("not_assessable", True)
    assert match.status == "not_enough_data"  # nothing was judged


def test_a_bank_with_conservative_is_soft_not_assessable() -> None:
    reason = only(run(profile(risk_preference="conservative"), financial=True), "debt")
    assert (reason.outcome, reason.hard) == ("not_assessable", False)


def test_missing_debt_data_is_no_data_and_a_hard_one_blocks_the_verdict() -> None:
    prefs = profile(debt_preference="avoid_high_debt", investment_style="quality")
    match = run(prefs, roe("30"))
    assert outcomes(match) == {"debt": "no_data", "quality": "pass"}
    assert (
        only(match, "debt").text == "Debt to equity is not in the data, so this cannot be judged."
    )
    assert match.status == "not_enough_data"


def test_missing_soft_data_allows_at_most_a_partial_match() -> None:
    """A match means every criterion that could be judged was checked and met: a soft criterion
    with no data leaves the verdict partial, never a full match (the lead's P14 decision)."""
    prefs = profile(risk_preference="conservative", investment_style="quality")
    match = run(prefs, roe("30"))
    assert outcomes(match) == {"debt": "no_data", "profit_growth": "no_data", "quality": "pass"}
    assert match.status == "partial"


def test_a_hard_fail_beats_a_hard_no_data_elsewhere() -> None:
    # only one hard rule exists, so pair it with a failing soft rule: fail still wins over partial
    prefs = profile(debt_preference="avoid_high_debt", investment_style="quality")
    assert run(prefs, *debt("200", "100"), roe("5")).status == "no_match"


# --- dividend -------------------------------------------------------------------------------------


def test_dividend_paid_passes_and_zero_misses() -> None:
    prefs = profile(investment_style="income")
    assert only(run(prefs, dividend("0.5")), "dividend").outcome == "pass"
    zero = run(prefs, dividend("0"))
    assert only(zero, "dividend").outcome == "miss"
    assert only(zero, "dividend").text.startswith("The latest dividend is ₹0 per share, so ")
    assert zero.status == "partial"


def test_dividend_missing_is_no_data() -> None:
    match = run(profile(investment_style="income"), roe("10"))
    assert only(match, "dividend").outcome == "no_data"
    assert only(match, "dividend").citations == ()


def test_dividend_in_dollars_keeps_its_currency() -> None:
    usd = fact(5, "dividend_per_share", "FY2026", "1.5", unit="USD_PER_SHARE", currency="USD")
    text = only(run(profile(investment_style="income"), usd), "dividend").text
    assert text.startswith("The latest dividend is US$1.5 per share")


def test_dividend_from_a_view_the_key_facts_do_not_show_falls_back_to_plain() -> None:
    older = fact(5, "dividend_per_share", "FY2025", "2", unit="INR_PER_SHARE")
    newer = fact(6, "dividend_per_share", "FY2026", "3", unit="INR_PER_SHARE", basis="standalone")
    text = only(run(profile(investment_style="income"), older, newer), "dividend").text
    assert text.startswith("The latest dividend is 3 per share")


# --- growth ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("after", "outcome"), [("110", "pass"), ("109.9", "miss"), ("100", "miss"), ("150", "pass")]
)
def test_growth_threshold_is_ten_percent(after: str, outcome: str) -> None:
    facts = pair(3, "revenue_from_operations", "100", after)
    reason = only(run(profile(investment_style="growth"), *facts), "revenue_growth")
    assert reason.outcome == outcome
    assert reason.hard is False
    assert len(reason.citations) == 2


def test_profit_growth_for_a_growth_style_needs_ten_percent() -> None:
    match = run(profile(investment_style="growth"), *pair(7, "net_profit", "100", "109"))
    reason = only(match, "profit_growth")
    assert (reason.outcome, reason.preference) == ("miss", "growth")
    assert reason.text == "Net profit growth is 9%, below the 10% wanted for a growth style."


@pytest.mark.parametrize(
    ("after", "outcome"), [("100", "pass"), ("101", "pass"), ("99", "miss"), ("90", "miss")]
)
def test_stability_needs_no_fall(after: str, outcome: str) -> None:
    match = run(profile(other_preferences="stability"), *pair(7, "net_profit", "100", after))
    assert only(match, "profit_growth").outcome == outcome
    assert only(match, "profit_growth").preference == "stability"


def test_negative_growth_text_shows_the_sign_and_the_floor() -> None:
    match = run(profile(other_preferences="stability"), *pair(7, "net_profit", "100", "90"))
    assert only(match, "profit_growth").text == (
        "Net profit growth is -10%, below the 0% floor (no fall) wanted."
    )


def test_conservative_alone_asks_for_no_fall() -> None:
    match = run(profile(risk_preference="conservative"), *pair(7, "net_profit", "100", "95"))
    reason = only(match, "profit_growth")
    assert (reason.outcome, reason.preference) == ("miss", "conservative")


def test_stability_is_named_before_conservative() -> None:
    prefs = profile(risk_preference="conservative", other_preferences="stability")
    assert only(run(prefs), "profit_growth").preference == "stability"


def test_several_profit_growth_asks_use_the_strictest_threshold() -> None:
    prefs = profile(
        risk_preference="conservative", investment_style="growth", other_preferences="stability"
    )
    match = run(
        prefs, *pair(7, "net_profit", "100", "105")
    )  # +5%: fine for no fall, not for growth
    profits = [r for r in match.reasons if r.criterion == "profit_growth"]
    assert len(profits) == 1
    assert (profits[0].outcome, profits[0].preference) == ("miss", "growth")


def test_growth_not_in_the_data_is_no_data() -> None:
    match = run(profile(investment_style="growth"), roe("10"))
    assert outcomes(match) == {"revenue_growth": "no_data", "profit_growth": "no_data"}
    assert only(match, "revenue_growth").text == (
        "Revenue growth is not in the data, so this cannot be judged."
    )
    assert match.status == "not_enough_data"


def test_a_bank_uses_net_interest_income_growth() -> None:
    match = run(
        profile(investment_style="growth"),
        *pair(3, "net_interest_income", "100", "105"),
        financial=True,
    )
    assert only(match, "revenue_growth").text.startswith("Net interest income growth is 5%")


# --- quality --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("roe_value", "outcome"), [("15", "pass"), ("14.9", "miss"), ("40", "pass")]
)
def test_quality_needs_fifteen_percent_roe(roe_value: str, outcome: str) -> None:
    reason = only(run(profile(investment_style="quality"), roe(roe_value)), "quality")
    assert reason.outcome == outcome


def test_quality_uses_the_latest_full_year() -> None:
    old = fact(6, "return_on_equity", "FY2025", "30", unit="PERCENT")
    new = fact(7, "return_on_equity", "FY2026", "10", unit="PERCENT")
    reason = only(run(profile(investment_style="quality"), old, new), "quality")
    assert (reason.outcome, reason.text.split(" is ")[0]) == ("miss", "Return on equity for FY2026")


def test_quality_without_roe_is_no_data() -> None:
    reason = only(run(profile(investment_style="quality")), "quality")
    assert reason.outcome == "no_data"
    assert reason.text == "Return on equity is not in the data, so this cannot be judged."


# --- cautions -------------------------------------------------------------------------------------


def test_negative_sentiment_is_a_caution_citing_the_latest_three_negative_events() -> None:
    events = [*NEGATIVE_NEWS, event(9, date(2026, 9, 5), "positive", "low")]
    match = match_stock(
        profile(investment_style="quality"), stock("DEMOX", [roe("20")], events=events), today=TODAY
    )
    (caution,) = match.cautions
    assert (caution.criterion, caution.preference, caution.hard, caution.outcome) == (
        "sentiment",
        "",
        False,
        "miss",
    )
    assert [c.quote for c in caution.citations] == ["quote 4", "quote 3", "quote 2"]
    assert "score" in caution.text
    assert "5 events" in caution.text
    assert match.status == "match"  # cautions never change the status


def test_positive_or_thin_news_is_no_caution() -> None:
    good = [event(i, date(2026, 9, i), "positive") for i in (1, 2, 3)]
    thin = NEGATIVE_NEWS[:2]  # fewer than three events: no sentiment at all
    prefs = profile(investment_style="quality")
    for events in (good, thin, []):
        match = match_stock(prefs, stock("DEMOX", [roe("20")], events=events), today=TODAY)
        assert match.cautions == ()


def test_reasons_come_in_criterion_order() -> None:
    prefs = profile(
        risk_preference="conservative",
        debt_preference="avoid_high_debt",
        investment_style=("momentum", "quality", "growth", "income", "value"),
        other_preferences="long_term",
    )
    match = match_stock(prefs, demo_stocks()[0], today=TODAY)
    assert [r.criterion for r in match.reasons] == [
        "debt",
        "dividend",
        "revenue_growth",
        "profit_growth",
        "quality",
        "value",
        "momentum",
        "horizon",
    ]


# --- prices (ADR 025): momentum, value and horizon ------------------------------------------------

MOMENTUM = profile(investment_style="momentum")
VALUE = profile(investment_style="value")
SHORT = profile(other_preferences="short_term")
LONG = profile(other_preferences="long_term")


def test_price_momentum_passes_at_exactly_zero_percent() -> None:
    reason = only(run(MOMENTUM, share_prices=prices("100", "100")), "momentum")
    assert reason.outcome == "pass"
    assert reason.text.startswith("Share price is up 0% over six months")


def test_price_momentum_misses_when_the_price_fell() -> None:
    reason = only(run(MOMENTUM, share_prices=prices("100", "90")), "momentum")
    assert reason.outcome == "miss"
    assert reason.text.startswith("Share price is down ")
    assert reason.hard is False


def test_price_momentum_wins_over_earnings_momentum() -> None:
    slowing = [*pair(7, "net_profit", "50", "52"), fact(20, "net_profit", "FY2024", "48")]
    reason = only(run(MOMENTUM, *slowing, share_prices=prices("100", "130")), "momentum")
    assert reason.outcome == "pass"
    assert "Share price" in reason.text


def test_short_price_history_falls_back_to_earnings_momentum() -> None:
    speeding = [*pair(7, "net_profit", "10", "13"), fact(20, "net_profit", "FY2024", "9")]
    reason = only(run(MOMENTUM, *speeding, share_prices=prices("100", "90", days=150)), "momentum")
    assert reason.outcome == "pass"  # the price fell but is not used
    assert "earnings momentum, used because six months of share prices" in reason.text


def test_history_just_short_of_six_months_falls_back() -> None:
    # the start (28 Mar 2026) is the earliest date needed; 184 rows reach only 29 Mar
    assert only(run(MOMENTUM, share_prices=prices("100", "120", days=184)), "momentum").outcome == (
        "no_data"
    )
    assert only(run(MOMENTUM, share_prices=prices("100", "120", days=185)), "momentum").outcome == (
        "pass"
    )


def test_price_momentum_is_measured_across_a_bonus_without_a_fake_crash() -> None:
    rows = prices("100", "100")
    for index in range(200, len(rows)):  # a 1:1 bonus: the price halves at row 200
        row = rows[index]
        half = row.close / 2
        rows[index] = DailyPrice(
            row.bse_code,
            row.trade_date,
            half,
            half,
            half,
            half,
            half,
            row.volume,
        )
    reason = only(run(MOMENTUM, share_prices=rows), "momentum")
    assert reason.outcome == "pass"
    assert reason.text.startswith("Share price is up 0% ")


@pytest.mark.parametrize(
    ("last", "eps_value", "outcome"),
    [("200", "10", "pass"), ("201", "10", "miss"), ("100", "5", "pass"), ("300", "10", "miss")],
)
def test_value_needs_a_pe_of_at_most_twenty(last: str, eps_value: str, outcome: str) -> None:
    match = run(VALUE, eps(eps_value), share_prices=prices(last, last))
    reason = only(match, "value")
    assert (reason.outcome, reason.hard, reason.preference) == (outcome, False, "value")


def test_value_without_eps_is_no_data() -> None:
    match = run(VALUE, roe("10"), share_prices=prices("100", "100"))
    reason = only(match, "value")
    assert reason.outcome == "no_data"
    assert reason.text == "Price to earnings is not in the data: no yearly basic EPS is on record."
    assert [c.label for c in reason.citations] == ["BSE daily price file · 28 Sep 2026"]


def test_value_without_prices_is_not_assessable_and_not_judged() -> None:
    match = run(VALUE, eps("10"))
    assert only(match, "value").outcome == "not_assessable"
    assert match.status == "not_enough_data"


def test_value_with_a_loss_is_not_assessable() -> None:
    reason = only(run(VALUE, eps("-2"), share_prices=prices("100", "100")), "value")
    assert reason.outcome == "not_assessable"
    assert "zero or negative" in reason.text


def test_value_after_a_bonus_since_the_eps_year_is_not_assessable() -> None:
    rows = prices("100", "100", days=400)
    # a 1:1 bonus on 1 May 2026, after FY2026 (year end 31 Mar 2026)
    index = next(i for i, row in enumerate(rows) if row.trade_date == date(2026, 5, 1))
    for i in range(index, len(rows)):
        row = rows[i]
        half = Decimal(50)
        rows[i] = DailyPrice(row.bse_code, row.trade_date, half, half, half, half, half, 1)
    reason = only(run(VALUE, eps("10"), share_prices=rows), "value")
    assert reason.outcome == "not_assessable"
    assert "bonus issue or split on 1 May 2026" in reason.text
    assert len(reason.citations) == 2  # the price and the EPS


def test_value_with_a_bonus_before_the_eps_year_end_is_judged() -> None:
    rows = prices("100", "100", days=400)
    index = next(i for i, row in enumerate(rows) if row.trade_date == date(2026, 2, 1))
    for i in range(index, len(rows)):
        row = rows[i]
        half = Decimal(50)
        rows[i] = DailyPrice(row.bse_code, row.trade_date, half, half, half, half, half, 1)
    assert only(run(VALUE, eps("10"), share_prices=rows), "value").outcome == "pass"


def test_short_term_needs_a_year_of_swings_of_at_most_thirty_percent() -> None:
    steady = only(run(SHORT, share_prices=prices("100", "110")), "horizon")
    assert (steady.outcome, steady.preference) == ("pass", "short_term")
    jumpy = only(run(SHORT, share_prices=prices("100", "110", zigzag="0.02")), "horizon")
    assert jumpy.outcome == "miss"


def test_short_term_without_enough_history_is_no_data() -> None:
    assert only(run(SHORT, share_prices=prices("100", "110", days=50)), "horizon").outcome == (
        "no_data"
    )
    assert only(run(SHORT), "horizon").outcome == "no_data"


def test_long_term_passes_when_net_profit_did_not_fall() -> None:
    rising = [*pair(7, "net_profit", "50", "50"), fact(20, "net_profit", "FY2024", "48")]
    reason = only(run(LONG, *rising), "horizon")
    assert (reason.outcome, reason.preference) == ("pass", "long_term")
    assert reason.text == (
        "Net profit did not fall over three years "
        "(FY2024 ₹48 crore, FY2025 ₹50 crore, FY2026 ₹50 crore)."
    )
    assert len(reason.citations) == 3


def test_long_term_misses_when_a_year_fell() -> None:
    dipping = [*pair(7, "net_profit", "50", "60"), fact(20, "net_profit", "FY2024", "55")]
    reason = only(run(LONG, *dipping), "horizon")
    assert reason.outcome == "miss"
    assert reason.text.startswith("Net profit fell in at least one year over three years")


def test_long_term_without_a_three_year_chain_is_no_data() -> None:
    reason = only(run(LONG, *pair(7, "net_profit", "50", "60")), "horizon")
    assert reason.outcome == "no_data"
    assert match_stock(LONG, stock("DEMOX", []), today=TODAY).status == "not_enough_data"


def test_long_term_is_used_when_both_horizons_are_asked() -> None:
    both = profile(other_preferences=("short_term", "long_term"))
    assert only(run(both, share_prices=prices("100", "110")), "horizon").preference == "long_term"
