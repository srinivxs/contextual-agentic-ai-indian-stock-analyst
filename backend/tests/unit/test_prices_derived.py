"""Values computed on read from stored prices (ADR 025): pure functions, synthetic numbers only."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.insights import Citation, KeyFact
from app.prices.derived import (
    adjusted_closes,
    corporate_actions,
    dividend_yield,
    latest_key_fact,
    pe_ratio,
    period_return,
    price_citation,
    snapshot,
    volatility,
)
from app.prices.model import CorporateAction, DailyPrice, bhavcopy_url

D = Decimal


def price(day: date, close: str, prev_close: str | None = None) -> DailyPrice:
    return DailyPrice(
        bse_code="500999",
        trade_date=day,
        open=D(close),
        high=D(close),
        low=D(close),
        close=D(close),
        prev_close=D(prev_close if prev_close is not None else close),
        volume=100,
    )


def series(start: date, closes: list[str]) -> list[DailyPrice]:
    """One row per calendar day; each prev_close is the day before's close (no action)."""
    rows: list[DailyPrice] = []
    for offset, close in enumerate(closes):
        prev = closes[offset - 1] if offset else close
        rows.append(price(start + timedelta(days=offset), close, prev))
    return rows


def key_fact(metric: str, period: str, value: str, unit: str = "INR_PER_SHARE") -> KeyFact:
    return KeyFact(
        metric=metric,
        label=metric,
        period=period,
        basis="consolidated",
        currency="INR" if unit.startswith("INR") else "USD",
        unit=unit,
        value=D(value),
        status="single",
        corroborated_by=0,
        citation=Citation("screener", f"screener.in · {metric}", "https://example.test/", None),
        disputed_by=(),
    )


# --- corporate actions ----------------------------------------------------------------------------


def test_no_actions_on_ordinary_days() -> None:
    assert corporate_actions(series(date(2026, 1, 1), ["100", "101", "99"])) == []


def test_empty_and_single_row_have_no_actions() -> None:
    assert corporate_actions([]) == []
    assert corporate_actions([price(date(2026, 1, 1), "100")]) == []


def test_a_one_to_one_bonus_is_a_half() -> None:
    rows = [
        price(date(2026, 1, 1), "100"),
        price(date(2026, 1, 2), "51", "50"),  # ex-bonus: BSE's previous close is already halved
    ]
    assert corporate_actions(rows) == [CorporateAction(date(2026, 1, 2), D("0.5"))]


def test_a_factor_within_one_percent_is_rounding() -> None:
    rows = [price(date(2026, 1, 1), "100"), price(date(2026, 1, 2), "100", "100.9")]
    assert corporate_actions(rows) == []
    rows = [price(date(2026, 1, 1), "100"), price(date(2026, 1, 2), "100", "101.5")]
    assert len(corporate_actions(rows)) == 1


def test_a_factor_of_exactly_the_tolerance_is_not_an_action() -> None:
    rows = [price(date(2026, 1, 1), "100"), price(date(2026, 1, 2), "100", "101")]
    assert corporate_actions(rows) == []


def test_a_zero_close_makes_no_action() -> None:
    rows = [price(date(2026, 1, 1), "0"), price(date(2026, 1, 2), "5", "5")]
    assert corporate_actions(rows) == []


def test_a_split_of_five_gives_a_fifth() -> None:
    rows = [price(date(2026, 1, 1), "500"), price(date(2026, 1, 2), "100", "100")]
    assert corporate_actions(rows)[0].factor == D("0.2")


# --- adjusted closes ------------------------------------------------------------------------------


def test_adjusted_closes_without_actions_equal_the_raw_closes() -> None:
    adjusted = adjusted_closes(series(date(2026, 1, 1), ["100", "110"]))
    assert [(a.close, a.raw_close) for a in adjusted] == [
        (D("100"), D("100")),
        (D("110"), D("110")),
    ]


def test_a_bonus_halves_every_earlier_close_and_never_looks_like_a_crash() -> None:
    rows = [
        price(date(2026, 1, 1), "100"),
        price(date(2026, 1, 2), "104", "100"),
        price(date(2026, 1, 3), "52", "52"),  # 1:1 bonus (previous close halved by BSE)
        price(date(2026, 1, 4), "54", "52"),
    ]
    adjusted = adjusted_closes(rows)
    assert [a.close for a in adjusted] == [D("50"), D("52"), D("52"), D("54")]
    assert [a.raw_close for a in adjusted] == [D("100"), D("104"), D("52"), D("54")]
    assert [a.trade_date for a in adjusted] == [r.trade_date for r in rows]


def test_two_actions_multiply() -> None:
    rows = [
        price(date(2026, 1, 1), "100"),
        price(date(2026, 1, 2), "50", "50"),
        price(date(2026, 1, 3), "25", "25"),
    ]
    assert [a.close for a in adjusted_closes(rows)] == [D("25"), D("25"), D("25")]


def test_adjusted_closes_of_nothing_is_nothing() -> None:
    assert adjusted_closes([]) == []


# --- period return --------------------------------------------------------------------------------


def daily(start: date, days: int, close: str = "100") -> list[DailyPrice]:
    return series(start, [close] * days)


def test_return_over_six_months() -> None:
    rows = [price(date(2026, 3, 28), "100"), price(date(2026, 9, 28), "112.34", "100")]
    adjusted = adjusted_closes(rows)
    assert period_return(adjusted, months=6, as_of=date(2026, 9, 28)) == D("12.3")


def test_return_can_be_negative() -> None:
    rows = [price(date(2026, 8, 28), "200"), price(date(2026, 9, 28), "150", "200")]
    assert period_return(adjusted_closes(rows), months=1, as_of=date(2026, 9, 28)) == D("-25.0")


def test_return_uses_the_last_close_on_or_before_the_start() -> None:
    rows = [
        price(date(2026, 3, 20), "100"),
        price(date(2026, 3, 29), "999", "100"),  # after the start, not used
        price(date(2026, 9, 28), "120", "999"),
    ]
    assert period_return(adjusted_closes(rows), months=6, as_of=date(2026, 9, 28)) == D("20.0")


def test_return_is_none_when_history_does_not_reach_back() -> None:
    rows = [price(date(2026, 4, 1), "100"), price(date(2026, 9, 28), "120", "100")]
    assert period_return(adjusted_closes(rows), months=6, as_of=date(2026, 9, 28)) is None


def test_return_is_none_with_no_history_or_a_zero_start() -> None:
    assert period_return([], months=1, as_of=date(2026, 9, 28)) is None
    rows = [price(date(2026, 8, 1), "0"), price(date(2026, 9, 28), "5", "0")]
    assert period_return(adjusted_closes(rows), months=1, as_of=date(2026, 9, 28)) is None


def test_month_end_dates_clamp() -> None:
    # 31 Aug minus 6 months is 28 Feb 2026 (no 31 Feb)
    rows = [price(date(2026, 2, 28), "100"), price(date(2026, 8, 31), "110", "100")]
    assert period_return(adjusted_closes(rows), months=6, as_of=date(2026, 8, 31)) == D("10.0")


def test_return_crosses_a_year_boundary() -> None:
    rows = [price(date(2025, 9, 28), "100"), price(date(2026, 9, 28), "150", "100")]
    assert period_return(adjusted_closes(rows), months=12, as_of=date(2026, 9, 28)) == D("50.0")


def test_return_across_a_bonus_is_not_a_crash() -> None:
    rows = [
        price(date(2026, 3, 1), "100"),
        price(date(2026, 6, 1), "50", "50"),  # 1:1 bonus, price otherwise flat
        price(date(2026, 9, 1), "55", "50"),
    ]
    assert period_return(adjusted_closes(rows), months=6, as_of=date(2026, 9, 1)) == D("10.0")


# --- volatility -----------------------------------------------------------------------------------


def test_a_flat_price_has_zero_volatility() -> None:
    assert volatility(adjusted_closes(daily(date(2026, 1, 1), 100))) == D("0.0")


def test_volatility_of_alternating_returns() -> None:
    closes = ["100", "101"] * 40  # 80 closes -> 79 returns of about +1% / -0.99%
    value = volatility(adjusted_closes(series(date(2026, 1, 1), closes)))
    assert value is not None
    assert D("15") < value < D("17")  # about 1% * sqrt(252) = 15.9%


def test_volatility_needs_sixty_returns() -> None:
    closes = ["100", "101"] * 31  # 62 closes = 61 returns
    assert volatility(adjusted_closes(series(date(2026, 1, 1), closes))) is not None
    assert volatility(adjusted_closes(series(date(2026, 1, 1), closes)[:61])) is not None  # 60
    assert volatility(adjusted_closes(series(date(2026, 1, 1), closes)[:60])) is None  # 59
    assert volatility([]) is None


def test_volatility_uses_only_the_last_days() -> None:
    wild = series(date(2024, 1, 1), ["100", "150"] * 40)  # long before the window
    calm = daily(date(2026, 1, 1), 100)
    assert volatility(adjusted_closes([*wild, *calm]), days=365) == D("0.0")


def test_volatility_is_not_polluted_by_a_bonus() -> None:
    rows = daily(date(2026, 1, 1), 70)
    rows.append(price(date(2026, 3, 12), "50", "50"))  # 1:1 bonus on a flat price
    rows.extend(series(date(2026, 3, 13), ["50"] * 5))
    assert volatility(adjusted_closes(rows)) == D("0.0")


# --- price citation -------------------------------------------------------------------------------


def test_a_price_is_cited_to_that_days_official_file() -> None:
    citation = price_citation(date(2026, 9, 28))
    assert citation == Citation(
        source="filing",
        label="BSE daily price file · 28 Sep 2026",
        url=bhavcopy_url(date(2026, 9, 28)),
        quote=None,
    )


# --- latest key fact ------------------------------------------------------------------------------


def test_latest_key_fact_is_the_first_of_the_metric() -> None:
    facts = [key_fact("eps_basic", "FY2026", "10"), key_fact("eps_basic", "FY2025", "9")]
    assert latest_key_fact(facts, "eps_basic") == facts[0]
    assert latest_key_fact(facts, "dividend_per_share") is None


# --- P/E and dividend yield -----------------------------------------------------------------------

LATEST = price(date(2026, 9, 28), "200", "199")
BONUS_AFTER_YEAR_END = CorporateAction(date(2026, 5, 1), D("0.5"))
BONUS_BEFORE_YEAR_END = CorporateAction(date(2026, 2, 1), D("0.5"))


def test_pe_is_close_over_latest_eps() -> None:
    eps = key_fact("eps_basic", "FY2026", "8")
    result = pe_ratio(LATEST, eps, [])
    assert (result.status, result.value, result.fact) == ("ok", D("25.0"), eps)
    assert result.reason == (
        "Share price ₹200 (28 Sep 2026) divided by basic EPS of ₹8 for FY2026 "
        "(latest full year on record)."
    )


def test_pe_is_rounded_to_one_decimal() -> None:
    assert pe_ratio(LATEST, key_fact("eps_basic", "FY2026", "7"), []).value == D("28.6")


def test_pe_without_eps_is_not_assessable() -> None:
    result = pe_ratio(LATEST, None, [])
    assert (result.status, result.value, result.fact) == ("not_assessable", None, None)
    assert "no yearly basic EPS" in result.reason


@pytest.mark.parametrize("eps", ["0", "-3"])
def test_pe_with_a_zero_or_negative_eps_is_not_assessable(eps: str) -> None:
    result = pe_ratio(LATEST, key_fact("eps_basic", "FY2026", eps), [])
    assert (result.status, result.value) == ("not_assessable", None)
    assert "zero or negative" in result.reason


def test_pe_with_eps_in_dollars_is_not_assessable() -> None:
    result = pe_ratio(LATEST, key_fact("eps_basic", "FY2026", "2", "USD_PER_SHARE"), [])
    assert result.status == "not_assessable"
    assert "not in rupees" in result.reason


def test_pe_after_a_bonus_since_the_eps_year_is_not_assessable() -> None:
    eps = key_fact("eps_basic", "FY2026", "8")  # year ended 31 Mar 2026
    result = pe_ratio(LATEST, eps, [BONUS_AFTER_YEAR_END])
    assert (result.status, result.value, result.fact) == ("not_assessable", None, eps)
    assert "1 May 2026" in result.reason
    assert "bonus issue or split" in result.reason


def test_pe_with_a_bonus_before_the_eps_year_end_is_fine() -> None:
    result = pe_ratio(LATEST, key_fact("eps_basic", "FY2026", "8"), [BONUS_BEFORE_YEAR_END])
    assert result.status == "ok"


def test_pe_with_a_bonus_on_the_year_end_day_is_fine() -> None:
    on_day = CorporateAction(date(2026, 3, 31), D("0.5"))
    assert pe_ratio(LATEST, key_fact("eps_basic", "FY2026", "8"), [on_day]).status == "ok"


def test_pe_with_an_unreadable_period_is_not_assessable() -> None:
    result = pe_ratio(LATEST, key_fact("eps_basic", "Q1FY2026", "8"), [])
    assert result.status == "not_assessable"


def test_pe_without_a_price_is_not_assessable() -> None:
    result = pe_ratio(None, key_fact("eps_basic", "FY2026", "8"), [])
    assert (result.status, result.value) == ("not_assessable", None)
    assert "no share price" in result.reason


def test_dividend_yield_is_dividend_over_close_in_percent() -> None:
    dividend = key_fact("dividend_per_share", "FY2026", "5")
    result = dividend_yield(LATEST, dividend, [])
    assert (result.status, result.value, result.fact) == ("ok", D("2.5"), dividend)
    assert result.reason == (
        "Dividend of ₹5 per share for FY2026 (latest full year on record) over the share "
        "price ₹200 (28 Sep 2026)."
    )


def test_a_dividend_of_zero_is_a_zero_yield() -> None:
    result = dividend_yield(LATEST, key_fact("dividend_per_share", "FY2026", "0"), [])
    assert (result.status, result.value) == ("ok", D("0.0"))


def test_dividend_yield_without_a_dividend_is_not_assessable() -> None:
    result = dividend_yield(LATEST, None, [])
    assert (result.status, result.value) == ("not_assessable", None)
    assert "no yearly dividend" in result.reason


def test_dividend_yield_after_a_bonus_is_not_assessable() -> None:
    dividend = key_fact("dividend_per_share", "FY2026", "5")
    result = dividend_yield(LATEST, dividend, [BONUS_AFTER_YEAR_END])
    assert result.status == "not_assessable"
    assert result.fact == dividend


def test_dividend_yield_without_a_price_or_in_dollars_is_not_assessable() -> None:
    assert dividend_yield(None, key_fact("dividend_per_share", "FY2026", "5"), []).status == (
        "not_assessable"
    )
    usd = key_fact("dividend_per_share", "FY2026", "1", "USD_PER_SHARE")
    assert "not in rupees" in dividend_yield(LATEST, usd, []).reason


def test_dividend_yield_with_a_zero_price_is_not_assessable() -> None:
    zero = price(date(2026, 9, 28), "0")
    result = dividend_yield(zero, key_fact("dividend_per_share", "FY2026", "5"), [])
    assert (result.status, result.value) == ("not_assessable", None)


# --- one stock's snapshot: what the prices API and the chat both show ---------------------------


def test_a_snapshot_gathers_the_latest_close_returns_volatility_and_ratios() -> None:
    rows = series(date(2025, 9, 1), [str(100 + n % 7) for n in range(400)])
    facts = [key_fact("eps_basic", "FY2026", "5"), key_fact("dividend_per_share", "FY2026", "2")]
    snap = snapshot(rows, facts)
    assert snap.latest == rows[-1]
    assert snap.day_change == D("-5.7")  # 106 -> 100
    assert list(snap.returns) == ["1m", "3m", "6m", "1y"]
    assert snap.returns["1m"] == period_return(
        adjusted_closes(rows), months=1, as_of=rows[-1].trade_date
    )
    assert snap.volatility == volatility(adjusted_closes(rows))
    assert snap.pe.status == "ok"
    assert snap.dividend_yield.status == "ok"
    assert snap.actions == []


def test_a_snapshot_of_no_prices_has_nothing_to_show() -> None:
    snap = snapshot([], [key_fact("eps_basic", "FY2026", "5")])
    assert (snap.latest, snap.day_change, snap.volatility) == (None, None, None)
    assert snap.returns == {"1m": None, "3m": None, "6m": None, "1y": None}
    assert snap.pe.status == "not_assessable"


def test_the_day_change_uses_bse_s_previous_close() -> None:
    rows = [price(date(2026, 9, 25), "200"), price(date(2026, 9, 28), "210", "200")]
    assert snapshot(rows, []).day_change == D("5.0")
