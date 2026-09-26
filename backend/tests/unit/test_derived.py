"""Values computed on read from stored facts and events (P11, ADR 009): never stored.

Which of several figures for the same fact wins, debt to equity, year-on-year growth, the latest
dividend, and rolling sentiment. All numbers are synthetic (DemoCo).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.derived import (
    SOURCE_RANK,
    Basis,
    Chosen,
    Currency,
    EventRow,
    EventSentiment,
    FactRow,
    Impact,
    Source,
    choose_all,
    choose_fact,
    debt_to_equity,
    growth_yoy,
    latest_dividend,
    rolling_sentiment,
)

AS_OF = date(2026, 9, 27)


def fact(
    fact_id: int,
    value: str,
    *,
    metric: str = "revenue",
    period: str = "FY2026",
    basis: Basis = "consolidated",
    currency: Currency = "INR",
    unit: str = "crore",
    source: Source = "annual_report",
    source_date: date | None = date(2026, 3, 31),
) -> FactRow:
    return FactRow(
        id=fact_id,
        metric=metric,
        period=period,
        period_end=date(2026, 3, 31),
        basis=basis,
        currency=currency,
        unit=unit,
        value=Decimal(value),
        source=source,
        source_date=source_date,
    )


# --- which figure wins ----------------------------------------------------------------------------


def test_the_source_ranking_is_the_owners_policy() -> None:
    assert SOURCE_RANK == {
        "annual_report": 0,
        "screener": 1,
        "presentation": 2,
        "transcript": 3,
        "announcement": 4,
    }


def test_no_candidates_means_no_choice() -> None:
    assert choose_fact([]) is None


def test_a_single_figure_wins_alone() -> None:
    only = fact(1, "100")
    assert choose_fact([only]) == Chosen(
        fact=only, status="single", corroborated_by=0, disagreeing=()
    )


@pytest.mark.parametrize(
    ("better", "worse"),
    [
        ("annual_report", "screener"),
        ("screener", "presentation"),
        ("presentation", "transcript"),
        ("transcript", "announcement"),
    ],
)
def test_a_better_ranked_source_wins_even_when_older(better: Source, worse: Source) -> None:
    old_good = fact(1, "100", source=better, source_date=date(2020, 1, 1))
    new_worse = fact(2, "100", source=worse, source_date=date(2026, 9, 1))
    chosen = choose_fact([new_worse, old_good])
    assert chosen is not None
    assert chosen.fact == old_good


def test_within_one_rank_the_latest_source_wins_and_an_unknown_date_is_oldest() -> None:
    undated = fact(9, "100", source="transcript", source_date=None)
    older = fact(1, "100", source="transcript", source_date=date(2025, 1, 1))
    newer = fact(2, "100", source="transcript", source_date=date(2026, 1, 1))
    chosen = choose_fact([undated, newer, older])
    assert chosen is not None
    assert chosen.fact == newer


def test_an_undated_figure_loses_to_a_dated_one_of_the_same_rank() -> None:
    undated = fact(9, "100", source="screener", source_date=None)
    dated = fact(1, "100", source="screener", source_date=date(2000, 1, 1))
    chosen = choose_fact([undated, dated])
    assert chosen is not None
    assert chosen.fact == dated


def test_a_full_tie_is_broken_by_the_highest_id() -> None:
    chosen = choose_fact([fact(3, "100"), fact(7, "100"), fact(5, "100")])
    assert chosen is not None
    assert chosen.fact.id == 7


def test_figures_within_one_percent_agree() -> None:
    winner = fact(1, "100")
    close = fact(2, "101", source="presentation")
    chosen = choose_fact([winner, close])
    assert chosen == Chosen(fact=winner, status="agreed", corroborated_by=1, disagreeing=())


def test_a_figure_just_over_one_percent_away_disagrees() -> None:
    winner = fact(1, "100")
    far = fact(2, "101.01", source="transcript")
    chosen = choose_fact([winner, far])
    assert chosen == Chosen(fact=winner, status="disputed", corroborated_by=0, disagreeing=(far,))


def test_agreement_and_disagreement_are_counted_separately() -> None:
    winner = fact(1, "-200")  # a loss: the 1% band is on the absolute value
    close = fact(2, "-201", source="presentation")
    far = fact(3, "-150", source="transcript")
    chosen = choose_fact([far, close, winner])
    assert chosen == Chosen(fact=winner, status="disputed", corroborated_by=1, disagreeing=(far,))


def test_two_zeros_agree_but_zero_and_a_small_number_do_not() -> None:
    zero = fact(1, "0")
    assert choose_fact([zero, fact(2, "0", source="screener")]) == Chosen(
        fact=zero, status="agreed", corroborated_by=1, disagreeing=()
    )
    small = fact(3, "0.01", source="screener")
    assert choose_fact([zero, small]) == Chosen(
        fact=zero, status="disputed", corroborated_by=0, disagreeing=(small,)
    )


def test_choose_all_keeps_bases_and_currencies_apart() -> None:
    consolidated = fact(1, "100", basis="consolidated")
    standalone = fact(2, "60", basis="standalone")
    in_usd = fact(3, "12", currency="USD")
    other_year = fact(4, "90", period="FY2025")
    other_metric = fact(5, "10", metric="net_profit")
    chosen = choose_all([consolidated, standalone, in_usd, other_year, other_metric])
    assert {key: c.fact.id for key, c in chosen.items()} == {
        ("revenue", "FY2026", "consolidated", "INR"): 1,
        ("revenue", "FY2026", "standalone", "INR"): 2,
        ("revenue", "FY2026", "consolidated", "USD"): 3,
        ("revenue", "FY2025", "consolidated", "INR"): 4,
        ("net_profit", "FY2026", "consolidated", "INR"): 5,
    }
    assert all(c.status == "single" for c in chosen.values())


def test_choose_all_of_nothing_is_empty() -> None:
    assert choose_all([]) == {}


# --- debt to equity -------------------------------------------------------------------------------


def borrowings(fact_id: int, value: str, **overrides: object) -> FactRow:
    return fact(fact_id, value, metric="total_borrowings", **overrides)  # type: ignore[arg-type]


def equity(fact_id: int, value: str, **overrides: object) -> FactRow:
    return fact(fact_id, value, metric="total_equity", **overrides)  # type: ignore[arg-type]


def test_debt_to_equity_does_not_apply_to_banks() -> None:
    result = debt_to_equity([borrowings(1, "500"), equity(2, "2000")], is_financial=True)
    assert result.status == "not_applicable"
    assert result.value is None
    assert result.fact_ids == ()
    assert result.reason == "Debt to equity does not apply to banks: borrowing is their business."


def test_debt_to_equity_is_borrowings_over_equity_rounded_to_two_places() -> None:
    result = debt_to_equity([borrowings(1, "1000"), equity(2, "3000")], is_financial=False)
    assert result.status == "ok"
    assert result.value == Decimal("0.33")
    assert result.fact_ids == (1, 2)
    assert "FY2026" in result.reason
    assert "consolidated" in result.reason


def test_debt_to_equity_rounds_half_up() -> None:
    result = debt_to_equity([borrowings(1, "1"), equity(2, "8")], is_financial=False)
    assert result.value == Decimal("0.13")  # 0.125


def test_a_debt_free_company_has_a_ratio_of_zero() -> None:
    result = debt_to_equity([borrowings(1, "0"), equity(2, "3000")], is_financial=False)
    assert result.status == "ok"
    assert result.value == Decimal("0.00")


def test_debt_to_equity_uses_the_latest_complete_year() -> None:
    rows = [
        borrowings(1, "100", period="FY2024"),
        equity(2, "100", period="FY2024"),
        borrowings(3, "200", period="FY2025"),
        equity(4, "400", period="FY2025"),
        borrowings(5, "999", period="FY2026"),  # FY2026 has no equity figure: incomplete
    ]
    result = debt_to_equity(rows, is_financial=False)
    assert result.value == Decimal("0.50")
    assert result.fact_ids == (3, 4)


def test_consolidated_is_preferred_over_standalone_over_unspecified() -> None:
    rows = [
        borrowings(1, "100", basis="unspecified"),
        equity(2, "100", basis="unspecified"),
        borrowings(3, "100", basis="standalone"),
        equity(4, "200", basis="standalone"),
        borrowings(5, "100", basis="consolidated"),
        equity(6, "400", basis="consolidated"),
    ]
    assert debt_to_equity(rows, is_financial=False).fact_ids == (5, 6)
    assert debt_to_equity(rows[:4], is_financial=False).fact_ids == (3, 4)
    assert debt_to_equity(rows[:2], is_financial=False).fact_ids == (1, 2)


def test_quarters_and_other_currencies_are_ignored() -> None:
    rows = [
        borrowings(1, "100", period="Q1FY2027"),
        equity(2, "100", period="Q1FY2027"),
        borrowings(3, "100", period="FY2027", currency="USD"),
        equity(4, "100", period="FY2027", currency="USD"),
        borrowings(5, "100", period="FY2026", currency=None),
        equity(6, "100", period="FY2026", currency=None),
        borrowings(7, "300", period="FY2025"),
        equity(8, "600", period="FY2025"),
    ]
    result = debt_to_equity(rows, is_financial=False)
    assert result.fact_ids == (7, 8)
    assert result.value == Decimal("0.50")


def test_the_chosen_figure_is_used_when_sources_disagree() -> None:
    rows = [
        borrowings(1, "100", source="annual_report"),
        borrowings(2, "900", source="transcript"),
        equity(3, "400"),
    ]
    result = debt_to_equity(rows, is_financial=False)
    assert result.value == Decimal("0.25")
    assert result.fact_ids == (1, 3)


def test_figures_in_different_units_are_not_divided() -> None:
    rows = [borrowings(1, "100", unit="crore"), equity(2, "400", unit="lakh")]
    result = debt_to_equity(rows, is_financial=False)
    assert result.status == "not_assessable"


@pytest.mark.parametrize(
    ("rows", "missing"),
    [
        ([equity(2, "400")], "total borrowings"),
        ([borrowings(1, "100")], "total equity"),
        ([], "total borrowings and total equity"),
    ],
)
def test_a_missing_input_is_named(rows: list[FactRow], missing: str) -> None:
    result = debt_to_equity(rows, is_financial=False)
    assert result.status == "not_assessable"
    assert result.value is None
    assert result.fact_ids == ()
    assert missing in result.reason


def test_inputs_on_different_bases_are_not_assessable() -> None:
    rows = [borrowings(1, "100", basis="consolidated"), equity(2, "400", basis="standalone")]
    result = debt_to_equity(rows, is_financial=False)
    assert result.status == "not_assessable"
    assert result.fact_ids == ()
    assert "same year" in result.reason


@pytest.mark.parametrize("value", ["0", "-50"])
def test_zero_or_negative_equity_is_not_assessable(value: str) -> None:
    rows = [
        borrowings(1, "100", period="FY2025"),
        equity(2, "400", period="FY2025"),  # an older good year is not used instead
        borrowings(3, "100"),
        equity(4, value),
    ]
    result = debt_to_equity(rows, is_financial=False)
    assert result.status == "not_assessable"
    assert result.value is None
    assert result.fact_ids == (3, 4)
    assert "zero or negative" in result.reason


# --- growth ---------------------------------------------------------------------------------------


def test_growth_is_the_latest_year_against_the_year_before() -> None:
    rows = [fact(1, "1000", period="FY2025"), fact(2, "1100", period="FY2026")]
    result = growth_yoy(rows, "revenue")
    assert result.status == "ok"
    assert result.value == Decimal("10.0")
    assert result.fact_ids == (1, 2)
    assert "FY2026" in result.reason
    assert "FY2025" in result.reason


def test_growth_is_rounded_to_one_decimal_and_can_be_negative() -> None:
    up = growth_yoy([fact(1, "1000", period="FY2025"), fact(2, "1234.56")], "revenue")
    assert up.value == Decimal("23.5")  # 23.456
    down = growth_yoy([fact(1, "1000", period="FY2025"), fact(2, "900")], "revenue")
    assert down.value == Decimal("-10.0")


def test_growth_uses_the_latest_year_that_has_a_pair() -> None:
    rows = [
        fact(1, "100", period="FY2023"),
        fact(2, "200", period="FY2024"),
        fact(3, "300", period="FY2025"),
        fact(4, "600", period="FY2026", basis="standalone"),  # no standalone FY2025
    ]
    result = growth_yoy(rows, "revenue")
    assert result.fact_ids == (2, 3)
    assert result.value == Decimal("50.0")


def test_growth_prefers_consolidated_then_inr_within_a_year() -> None:
    rows = [
        fact(1, "10", period="FY2025", currency="USD"),
        fact(2, "20", currency="USD"),
        fact(3, "100", period="FY2025", basis="standalone"),
        fact(4, "150", basis="standalone"),
        fact(5, "1000", period="FY2025"),
        fact(6, "1200"),
    ]
    assert growth_yoy(rows, "revenue").fact_ids == (5, 6)
    only_usd_and_standalone = rows[:4]
    assert growth_yoy(only_usd_and_standalone, "revenue").fact_ids == (1, 2)


def test_growth_needs_the_same_basis_and_unit_on_both_sides() -> None:
    mixed_basis = [fact(1, "1000", period="FY2025", basis="standalone"), fact(2, "1100")]
    assert growth_yoy(mixed_basis, "revenue").status == "not_assessable"
    mixed_unit = [fact(1, "1000", period="FY2025", unit="lakh"), fact(2, "1100")]
    assert growth_yoy(mixed_unit, "revenue").status == "not_assessable"


def test_without_an_annual_pair_growth_is_a_quarter_against_the_same_quarter_a_year_earlier() -> (
    None
):
    rows = [
        fact(1, "100", period="Q3FY2025"),
        fact(2, "125", period="Q3FY2026"),
        fact(3, "110", period="Q2FY2026"),  # the quarter before is not a comparison
        fact(4, "500", period="FY2026"),  # a year with no year before it
    ]
    result = growth_yoy(rows, "revenue")
    assert result.status == "ok"
    assert result.value == Decimal("25.0")
    assert result.fact_ids == (1, 2)
    assert "Q3FY2026" in result.reason


def test_the_latest_quarter_pair_is_used() -> None:
    rows = [
        fact(1, "100", period="Q3FY2025"),
        fact(2, "125", period="Q3FY2026"),
        fact(3, "100", period="Q1FY2026"),
        fact(4, "150", period="Q1FY2027"),
        fact(5, "100", period="Q4FY2025"),
        fact(6, "110", period="Q4FY2026"),
    ]
    assert growth_yoy(rows, "revenue").fact_ids == (3, 4)


def test_an_annual_pair_beats_a_later_quarter_pair() -> None:
    rows = [
        fact(1, "100", period="FY2025"),
        fact(2, "120", period="FY2026"),
        fact(3, "100", period="Q1FY2026"),
        fact(4, "150", period="Q1FY2027"),
    ]
    assert growth_yoy(rows, "revenue").fact_ids == (1, 2)


@pytest.mark.parametrize("prior", ["0", "-100"])
def test_a_zero_or_negative_prior_is_not_assessable(prior: str) -> None:
    rows = [fact(1, prior, period="FY2025"), fact(2, "100")]
    result = growth_yoy(rows, "revenue")
    assert result.status == "not_assessable"
    assert result.value is None
    assert result.fact_ids == (1, 2)
    assert "zero or negative" in result.reason


def test_growth_without_a_comparable_pair_is_not_assessable() -> None:
    rows = [fact(1, "100"), fact(2, "90", metric="net_profit", period="FY2025")]
    result = growth_yoy(rows, "revenue")
    assert result.status == "not_assessable"
    assert result.value is None
    assert result.fact_ids == ()
    assert "revenue" in result.reason


def test_growth_only_looks_at_the_asked_metric() -> None:
    rows = [
        fact(1, "100", metric="net_profit", period="FY2025"),
        fact(2, "300", metric="net_profit"),
        fact(3, "1000", period="FY2025"),
        fact(4, "1100"),
    ]
    assert growth_yoy(rows, "net_profit").value == Decimal("200.0")


def test_a_period_that_is_neither_a_year_nor_a_quarter_is_ignored() -> None:
    rows = [fact(1, "100", period="H1FY2025"), fact(2, "200", period="H1FY2026")]
    assert growth_yoy(rows, "revenue").status == "not_assessable"


# --- dividend -------------------------------------------------------------------------------------


def dividend(fact_id: int, value: str, **overrides: object) -> FactRow:
    return fact(fact_id, value, metric="dividend_per_share", unit="per_share", **overrides)  # type: ignore[arg-type]


def test_the_latest_dividend_is_the_latest_years_figure() -> None:
    rows = [
        dividend(1, "10", period="FY2025"),
        dividend(2, "12"),
        dividend(3, "5", period="Q1FY2027"),  # an interim quarter is not a year's dividend
    ]
    result = latest_dividend(rows)
    assert result.status == "ok"
    assert result.value == Decimal("12")
    assert result.fact_ids == (2,)
    assert "FY2026" in result.reason


def test_inr_is_preferred_then_consolidated_within_the_latest_year() -> None:
    rows = [
        dividend(1, "1", currency="USD"),
        dividend(2, "12", basis="standalone"),
        dividend(3, "12", basis="consolidated"),
        dividend(4, "99", period="FY2025"),
    ]
    assert latest_dividend(rows).fact_ids == (3,)
    assert latest_dividend([rows[0], rows[3]]).fact_ids == (1,)  # the latest year still wins


def test_no_dividend_on_record_is_insufficient_data() -> None:
    result = latest_dividend([fact(1, "100")])
    assert result.status == "insufficient_data"
    assert result.value is None
    assert result.fact_ids == ()


# --- rolling sentiment ----------------------------------------------------------------------------


def event(
    event_id: int,
    sentiment: EventSentiment,
    *,
    days_ago: int = 0,
    impact: Impact = "low",
) -> EventRow:
    return EventRow(
        id=event_id,
        event_type="results",
        sentiment=sentiment,
        impact=impact,
        event_date=AS_OF - timedelta(days=days_ago),
    )


def test_fewer_than_three_events_is_insufficient_data() -> None:
    result = rolling_sentiment([event(1, "positive"), event(2, "positive")], as_of=AS_OF)
    assert result.status == "insufficient_data"
    assert result.score is None
    assert result.label is None
    assert result.event_ids == (1, 2)


def test_all_positive_events_score_one() -> None:
    events = [event(n, "positive", days_ago=n * 10) for n in (1, 2, 3)]
    result = rolling_sentiment(events, as_of=AS_OF)
    assert result.status == "ok"
    assert result.score == 1.0
    assert result.label == "positive"
    assert result.event_ids == (1, 2, 3)


def test_all_negative_events_score_minus_one() -> None:
    events = [event(n, "negative", impact="high") for n in (1, 2, 3)]
    result = rolling_sentiment(events, as_of=AS_OF)
    assert result.score == -1.0
    assert result.label == "negative"


def test_events_outside_the_window_or_after_as_of_are_left_out() -> None:
    events = [
        event(1, "positive"),
        event(2, "positive", days_ago=365),  # the window's last day still counts
        event(3, "negative", days_ago=366),
        event(4, "negative", days_ago=-1),  # after as_of: not known yet
    ]
    result = rolling_sentiment(events, as_of=AS_OF)
    assert result.status == "insufficient_data"
    assert result.event_ids == (1, 2)


def test_impact_weighs_an_event() -> None:
    events = [
        event(1, "negative", impact="high"),  # weight 3
        event(2, "positive", impact="medium"),  # weight 2
        event(3, "positive", impact="low"),  # weight 1
    ]
    result = rolling_sentiment(events, as_of=AS_OF)
    assert result.score == 0.0  # (-3 + 2 + 1) / 6
    assert result.label == "mixed"


def test_an_older_event_counts_half_after_one_half_life() -> None:
    events = [
        event(1, "positive"),  # weight 1
        event(2, "negative", days_ago=90),  # weight 0.5
        event(3, "neutral"),  # weight 1
    ]
    result = rolling_sentiment(events, as_of=AS_OF)
    assert result.score == 0.2  # (1 - 0.5) / 2.5
    assert result.label == "mixed"


def test_the_score_is_rounded_to_two_decimals() -> None:
    events = [event(1, "positive"), event(2, "neutral"), event(3, "neutral")]
    assert rolling_sentiment(events, as_of=AS_OF).score == 0.33


@pytest.mark.parametrize(
    ("first", "label"),
    [("negative", "negative"), ("positive", "positive")],
)
def test_a_score_of_exactly_a_quarter_takes_the_label(first: EventSentiment, label: str) -> None:
    events = [event(1, first), event(2, "neutral"), event(3, "neutral"), event(4, "neutral")]
    result = rolling_sentiment(events, as_of=AS_OF)
    assert abs(result.score or 0) == 0.25
    assert result.label == label


def test_the_window_half_life_and_minimum_are_parameters() -> None:
    events = [event(1, "negative", days_ago=20), event(2, "positive", days_ago=40)]
    result = rolling_sentiment(events, as_of=AS_OF, window_days=30, half_life_days=10, min_events=1)
    assert result.status == "ok"
    assert result.score == -1.0
    assert result.event_ids == (1,)
