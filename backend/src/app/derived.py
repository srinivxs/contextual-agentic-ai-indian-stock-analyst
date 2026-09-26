"""Values computed on read from stored facts and events, never stored (P11, ADR 009).

Every function here is pure: stored rows in, an answer out, with the IDs of the rows it used so
an answer can cite them. Nothing is cached, so a corrected or deleted fact changes the answer at
once.

Which figure wins (choose_fact, the owner's conflict policy). Several filings may state the same
fact (one metric, period, basis and currency). The winner is:

1. the best-ranked source (SOURCE_RANK): the audited annual report, then screener.in's
   fundamentals table, then presentations, then earnings calls, then announcements;
2. within one rank, the latest source (its filing's period date or screener's fetch date; an
   unknown date counts as the oldest);
3. on a full tie, the highest row ID (the newest row), so the answer never depends on row order.

Another figure "agrees" when it is within 1% of the winner (two zeros agree). The result is
"single" (no other figure), "agreed" (all others agree) or "disputed" (at least one does not; the
disagreeing figures are returned so the UI can show them). Consolidated and standalone figures
never compete: they are different facts.

Debt to equity = total borrowings / total equity, to 2 decimals:

- "not applicable" for banks and other financial companies: borrowing is their business;
- the latest full fiscal year (FYxxxx, never a quarter) where both figures exist in INR on the same
  basis and in the same unit, preferring consolidated, then standalone, then unspecified;
- "not assessable" when no such year exists (the reason names the missing figure) or when that
  year's equity is zero or negative (an older year is not used instead).

Growth (growth_yoy) = the percent change, to 1 decimal, of one metric between the latest full year
and the year before; only if no such pair exists, the latest quarter against the same quarter a
year earlier. Both figures share basis, currency and unit (consolidated first, then INR). A
missing pair or an earlier figure of zero or less is "not assessable".

Latest dividend = the chosen dividend per share of the latest full year (INR first, then
consolidated); none on record is "insufficient data".

Rolling sentiment = a weighted mean of the events of the last window_days up to as_of
(negative -1, neutral 0, positive +1). Each event weighs its impact (low 1, medium 2, high 3)
times 0.5 ** (age in days / half_life_days): an event one half-life old counts half. The score is
rounded to 2 decimals and labelled negative at -0.25 or below, positive at 0.25 or above, mixed in
between. Fewer than min_events events is "insufficient data", not a number.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Source = Literal["annual_report", "screener", "presentation", "transcript", "announcement"]
Basis = Literal["consolidated", "standalone", "unspecified"]
Currency = Literal["INR", "USD"] | None
Impact = Literal["low", "medium", "high"]
EventSentiment = Literal["negative", "neutral", "positive"]
Key = tuple[str, str, str, str | None]  # (metric, period, basis, currency)

SOURCE_RANK: dict[Source, int] = {
    "annual_report": 0,
    "screener": 1,
    "presentation": 2,
    "transcript": 3,
    "announcement": 4,
}
BASIS_PREFERENCE: tuple[Basis, ...] = ("consolidated", "standalone", "unspecified")
CURRENCY_PREFERENCE: tuple[Currency, ...] = ("INR", "USD", None)
# How a reason names the kind of source a growth pair came from.
SOURCE_WORDS = {
    "annual_report": "annual reports",
    "screener": "screener.in",
    "presentation": "investor presentations",
    "transcript": "earnings calls",
    "announcement": "announcements",
}
AGREEMENT = Decimal("0.01")  # two figures within 1% of the winner agree

BORROWINGS = "total_borrowings"
EQUITY = "total_equity"
DIVIDEND = "dividend_per_share"

IMPACT_WEIGHT: dict[Impact, int] = {"low": 1, "medium": 2, "high": 3}
SENTIMENT_VALUE: dict[EventSentiment, int] = {"negative": -1, "neutral": 0, "positive": 1}
LABEL_THRESHOLD = 0.25

_PERIOD = re.compile(r"^(?:Q([1-4]))?FY(\d{4})$")


@dataclass(frozen=True)
class FactRow:
    id: int
    metric: str
    period: str  # "FY2026" or "Q3FY2026"
    period_end: date
    basis: Basis
    currency: Currency
    unit: str
    value: Decimal
    source: Source
    source_date: date | None  # the filing's period date, or screener's fetch date


@dataclass(frozen=True)
class Chosen:
    fact: FactRow
    status: Literal["single", "agreed", "disputed"]
    corroborated_by: int  # how many other figures agree within 1%
    disagreeing: tuple[FactRow, ...]


@dataclass(frozen=True)
class Derived:
    status: Literal["ok", "not_applicable", "not_assessable", "insufficient_data"]
    value: Decimal | None
    reason: str  # a plain sentence for the UI
    fact_ids: tuple[int, ...]


@dataclass(frozen=True)
class EventRow:
    id: int
    event_type: str
    sentiment: EventSentiment
    impact: Impact
    event_date: date


@dataclass(frozen=True)
class Sentiment:
    status: Literal["ok", "insufficient_data"]
    score: float | None  # -1 (all negative) to 1 (all positive)
    label: Literal["negative", "mixed", "positive"] | None
    event_ids: tuple[int, ...]


# --- periods --------------------------------------------------------------------------------------


def parse_period(period: str) -> tuple[int, int] | None:
    """ "FY2026" -> (2026, 0), a full year; "Q3FY2026" -> (2026, 3); anything else -> None."""
    match = _PERIOD.match(period)
    if match is None:
        return None
    quarter = int(match.group(1)) if match.group(1) else 0
    return int(match.group(2)), quarter


def format_period(year: int, quarter: int) -> str:
    return f"FY{year}" if quarter == 0 else f"Q{quarter}FY{year}"


# --- which figure wins ----------------------------------------------------------------------------


def _preference(fact: FactRow) -> tuple[int, date, int]:
    """Bigger is better: the best-ranked source, then the latest, then the newest row."""
    return (-SOURCE_RANK[fact.source], fact.source_date or date.min, fact.id)


def agrees(value: Decimal, winner: Decimal) -> bool:
    return abs(value - winner) <= abs(winner) * AGREEMENT


def _choose(candidates: list[FactRow]) -> Chosen:
    ordered = sorted(candidates, key=_preference, reverse=True)
    winner, others = ordered[0], ordered[1:]
    disagreeing = tuple(other for other in others if not agrees(other.value, winner.value))
    if not others:
        status: Literal["single", "agreed", "disputed"] = "single"
    elif disagreeing:
        status = "disputed"
    else:
        status = "agreed"
    return Chosen(
        fact=winner,
        status=status,
        corroborated_by=len(others) - len(disagreeing),
        disagreeing=disagreeing,
    )


def choose_fact(candidates: list[FactRow]) -> Chosen | None:
    """The winning figure among candidates for one (metric, period, basis, currency)."""
    return _choose(candidates) if candidates else None


def choose_all(rows: list[FactRow]) -> dict[Key, Chosen]:
    groups: dict[Key, list[FactRow]] = defaultdict(list)
    for row in rows:
        groups[(row.metric, row.period, row.basis, row.currency)].append(row)
    return {key: _choose(candidates) for key, candidates in groups.items()}


# --- debt to equity -------------------------------------------------------------------------------


def _years_in_inr(chosen: dict[Key, Chosen], metrics: tuple[str, ...]) -> list[int]:
    """The full fiscal years with an INR figure for any of the metrics, latest first."""
    years = set()
    for metric, period, _basis, currency in chosen:
        parsed = parse_period(period)
        if metric in metrics and currency == "INR" and parsed is not None and parsed[1] == 0:
            years.add(parsed[0])
    return sorted(years, reverse=True)


def _ratio(debt: FactRow, equity: FactRow) -> Derived:
    ids = (debt.id, equity.id)
    if equity.value <= 0:
        reason = (
            f"Not assessable: total equity for {equity.period} ({equity.basis}) is zero or "
            "negative, so the ratio has no meaning."
        )
        return Derived(status="not_assessable", value=None, reason=reason, fact_ids=ids)
    value = (debt.value / equity.value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    reason = (
        f"Total borrowings divided by total equity for {equity.period}, "
        f"{equity.basis} figures in INR."
    )
    return Derived(status="ok", value=value, reason=reason, fact_ids=ids)


def _missing_inputs(chosen: dict[Key, Chosen]) -> str:
    missing = [
        name
        for metric, name in ((BORROWINGS, "total borrowings"), (EQUITY, "total equity"))
        if not _years_in_inr(chosen, (metric,))
    ]
    if missing:
        return f"Not assessable: no yearly {' and '.join(missing)} figure in INR is on record."
    return (
        "Not assessable: total borrowings and total equity are never on record for the same "
        "year, on the same basis and in the same unit."
    )


def debt_to_equity(rows: list[FactRow], *, is_financial: bool) -> Derived:
    if is_financial:
        reason = "Debt to equity does not apply to banks: borrowing is their business."
        return Derived(status="not_applicable", value=None, reason=reason, fact_ids=())
    chosen = choose_all(rows)
    for year in _years_in_inr(chosen, (BORROWINGS, EQUITY)):
        for basis in BASIS_PREFERENCE:
            debt = chosen.get((BORROWINGS, format_period(year, 0), basis, "INR"))
            equity = chosen.get((EQUITY, format_period(year, 0), basis, "INR"))
            if debt is not None and equity is not None and debt.fact.unit == equity.fact.unit:
                return _ratio(debt.fact, equity.fact)
    return Derived(status="not_assessable", value=None, reason=_missing_inputs(chosen), fact_ids=())


# --- growth ---------------------------------------------------------------------------------------


def growth_yoy(rows: list[FactRow], metric: str) -> Derived:
    words = metric.replace("_", " ")
    # Every comparable (prior, current) pair WITHIN ONE KIND OF SOURCE (found in the first real
    # run: an annual report's figure against screener.in's for the year before measured a change
    # of definitions, not growth), with how much we prefer it: a full year over a quarter, then
    # the latest, then the best-ranked source, then consolidated, then INR.
    pairs: list[tuple[tuple[bool, int, int, int, int, int], FactRow, FactRow]] = []
    for source, rank in SOURCE_RANK.items():
        chosen = choose_all([row for row in rows if row.metric == metric and row.source == source])
        for later in (winner.fact for winner in chosen.values()):
            parsed = parse_period(later.period)
            if parsed is None:
                continue
            year, quarter = parsed
            earlier_key = (metric, format_period(year - 1, quarter), later.basis, later.currency)
            earlier = chosen.get(earlier_key)
            if earlier is None or earlier.fact.unit != later.unit:
                continue
            preference = (
                quarter == 0,
                year,
                quarter,
                -rank,
                -BASIS_PREFERENCE.index(later.basis),
                -CURRENCY_PREFERENCE.index(later.currency),
            )
            pairs.append((preference, earlier.fact, later))
    if not pairs:
        reason = (
            f"Not assessable: no two comparable periods of {words} (a year and the year "
            "before, or a quarter and the same quarter a year earlier, on the same basis)."
        )
        return Derived(status="not_assessable", value=None, reason=reason, fact_ids=())
    _, prior, current = max(pairs, key=lambda pair: pair[0])
    ids = (prior.id, current.id)
    if prior.value <= 0:
        reason = (
            f"Not assessable: {words} for {prior.period} is zero or negative, so a percent "
            "change has no meaning."
        )
        return Derived(status="not_assessable", value=None, reason=reason, fact_ids=ids)
    change = (current.value - prior.value) / prior.value * 100
    value = change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    reason = (
        f"Change in {words} from {prior.period} to {current.period}, {current.basis} figures "
        f"from {SOURCE_WORDS[current.source]}."
    )
    return Derived(status="ok", value=value, reason=reason, fact_ids=ids)


# --- dividend -------------------------------------------------------------------------------------


def latest_dividend(rows: list[FactRow]) -> Derived:
    # every full year's chosen figure, with how much we prefer it: the latest year, then INR,
    # then consolidated
    candidates: list[tuple[tuple[int, int, int], FactRow]] = []
    for chosen in choose_all([row for row in rows if row.metric == DIVIDEND]).values():
        fact = chosen.fact
        parsed = parse_period(fact.period)
        if parsed is None or parsed[1] != 0:
            continue
        preference = (
            parsed[0],
            -CURRENCY_PREFERENCE.index(fact.currency),
            -BASIS_PREFERENCE.index(fact.basis),
        )
        candidates.append((preference, fact))
    if not candidates:
        reason = "No yearly dividend per share is on record."
        return Derived(status="insufficient_data", value=None, reason=reason, fact_ids=())
    _, fact = max(candidates, key=lambda candidate: candidate[0])
    reason = f"Dividend per share for {fact.period}, the latest year on record."
    return Derived(status="ok", value=fact.value, reason=reason, fact_ids=(fact.id,))


# --- rolling sentiment ----------------------------------------------------------------------------


def _label(score: float) -> Literal["negative", "mixed", "positive"]:
    if score <= -LABEL_THRESHOLD:
        return "negative"
    if score >= LABEL_THRESHOLD:
        return "positive"
    return "mixed"


def rolling_sentiment(
    events: list[EventRow],
    *,
    as_of: date,
    window_days: int = 365,
    half_life_days: int = 90,
    min_events: int = 3,
) -> Sentiment:
    recent = [e for e in events if 0 <= (as_of - e.event_date).days <= window_days]
    ids = tuple(e.id for e in recent)
    if len(recent) < min_events:
        return Sentiment(status="insufficient_data", score=None, label=None, event_ids=ids)
    weighted_sum = 0.0
    total_weight = 0.0
    for e in recent:
        age_days = (as_of - e.event_date).days
        weight = IMPACT_WEIGHT[e.impact] * math.pow(0.5, age_days / half_life_days)
        weighted_sum += weight * SENTIMENT_VALUE[e.sentiment]
        total_weight += weight
    score = round(weighted_sum / total_weight, 2)  # the label follows the score as shown
    return Sentiment(status="ok", score=score, label=_label(score), event_ids=ids)
