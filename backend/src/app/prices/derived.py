"""Values computed on read from stored daily prices (ADR 025, ADR 009): never stored.

Pure functions: rows in, an answer out.

- corporate_actions: for two consecutive stored days, factor = prev_close(later) / close(earlier).
  BSE gives the previous close already adjusted on an ex-date, our stored close is raw, so the
  ratio is 1 on an ordinary day and 0.5 after a 1:1 bonus. Kept when it differs from 1 by more
  than ACTION_TOLERANCE (1%: rounding).
- adjusted_closes: every close multiplied by the factors of the actions AFTER it, so a bonus never
  looks like a crash.
- period_return: percent change from the last close on or before (latest date minus N calendar
  months) to the latest close; None when the history does not reach back that far.
- volatility: standard deviation of daily simple returns of the adjusted closes over the last
  365 days, times sqrt(252), in percent; None with fewer than 60 returns.
- pe_ratio / dividend_yield: close over the latest full-year basic EPS, or the latest dividend over
  close (percent). Not assessable when the figure is missing, not in rupees, not positive (P/E),
  or when a bonus or split happened after that year ended: a per-share figure from before it no
  longer compares with today's price.
"""

import math
import statistics
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Literal

from app.derived import parse_period
from app.insights import Citation, KeyFact
from app.prices.model import (
    ACTION_TOLERANCE,
    PRICE_SOURCE_LABEL,
    AdjustedClose,
    CorporateAction,
    DailyPrice,
    bhavcopy_url,
)

MIN_RETURNS = 60  # fewer daily returns than this is not enough for a volatility
TRADING_DAYS = 252
ONE_DECIMAL = Decimal("0.1")
FACTOR_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class PriceValue:
    """P/E or dividend yield: a value, the per-share fact it used, and a plain reason."""

    status: Literal["ok", "not_assessable"]
    value: Decimal | None
    reason: str
    fact: KeyFact | None


RETURN_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "1y": 12}


@dataclass(frozen=True)
class PriceSnapshot:
    """What one stock's prices say today: the prices API and the chat both show this."""

    latest: DailyPrice | None
    day_change: Decimal | None  # percent against BSE's previous close (adjusted for any action)
    returns: dict[str, Decimal | None]  # RETURN_MONTHS keys, percent, adjusted
    volatility: Decimal | None
    pe: PriceValue
    dividend_yield: PriceValue
    actions: list[CorporateAction]


def snapshot(prices: list[DailyPrice], facts: list[KeyFact]) -> PriceSnapshot:
    """prices oldest first; facts from key_facts (for the EPS and the dividend)."""
    latest = prices[-1] if prices else None
    actions = corporate_actions(prices)
    adjusted = adjusted_closes(prices)
    returns: dict[str, Decimal | None] = {name: None for name in RETURN_MONTHS}
    day_change = None
    if latest is not None:
        returns = {
            name: period_return(adjusted, months=months, as_of=latest.trade_date)
            for name, months in RETURN_MONTHS.items()
        }
        day_change = _one_decimal((latest.close / latest.prev_close - 1) * 100)
    return PriceSnapshot(
        latest=latest,
        day_change=day_change,
        returns=returns,
        volatility=volatility(adjusted),
        pe=pe_ratio(latest, latest_key_fact(facts, "eps_basic"), actions),
        dividend_yield=dividend_yield(
            latest, latest_key_fact(facts, "dividend_per_share"), actions
        ),
        actions=actions,
    )


def price_citation(day: date) -> Citation:
    """ "BSE daily price file · 28 Sep 2026", linking to that day's official file."""
    return Citation(
        source="filing",
        label=f"{PRICE_SOURCE_LABEL} · {day:%d %b %Y}",
        url=bhavcopy_url(day),
        quote=None,
    )


def _one_decimal(value: Decimal) -> Decimal:
    return value.quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)


# --- corporate actions and adjusted closes --------------------------------------------------------


def corporate_actions(prices: list[DailyPrice]) -> list[CorporateAction]:
    actions: list[CorporateAction] = []
    for earlier, later in pairwise(prices):
        if earlier.close <= 0:
            continue
        factor = later.prev_close / earlier.close
        if abs(factor - 1) > ACTION_TOLERANCE:
            actions.append(
                CorporateAction(later.trade_date, factor.quantize(FACTOR_PLACES, ROUND_HALF_UP))
            )
    return actions


def adjusted_closes(prices: list[DailyPrice]) -> list[AdjustedClose]:
    factor_on = {action.trade_date: action.factor for action in corporate_actions(prices)}
    adjusted: list[AdjustedClose] = []
    running = Decimal(1)  # the product of the factors of the actions after the row
    for row in reversed(prices):
        adjusted.append(AdjustedClose(row.trade_date, row.close * running, row.close))
        running *= factor_on.get(row.trade_date, Decimal(1))
    adjusted.reverse()
    return adjusted


# --- returns and volatility -----------------------------------------------------------------------


def _months_before(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def period_return(adjusted: list[AdjustedClose], *, months: int, as_of: date) -> Decimal | None:
    if not adjusted:
        return None
    start = _months_before(as_of, months)
    base = next((row for row in reversed(adjusted) if row.trade_date <= start), None)
    if base is None or base.close <= 0:
        return None
    change = (adjusted[-1].close / base.close - 1) * 100
    return _one_decimal(change)


def volatility(adjusted: list[AdjustedClose], *, days: int = 365) -> Decimal | None:
    if not adjusted:
        return None
    since = adjusted[-1].trade_date - timedelta(days=days)
    window = [row for row in adjusted if row.trade_date >= since]
    returns = [
        float(later.close / earlier.close - 1)
        for earlier, later in pairwise(window)
        if earlier.close > 0
    ]
    if len(returns) < MIN_RETURNS:
        return None
    annual = statistics.stdev(returns) * math.sqrt(TRADING_DAYS) * 100
    return _one_decimal(Decimal(repr(annual)))


# --- P/E and dividend yield -----------------------------------------------------------------------


def latest_key_fact(facts: list[KeyFact], metric: str) -> KeyFact | None:
    """The metric's latest full-year view (key_facts lists each metric's newest year first)."""
    return next((fact for fact in facts if fact.metric == metric), None)


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _no(reason: str, fact: KeyFact | None = None) -> PriceValue:
    return PriceValue("not_assessable", None, reason, fact)


def _blocked(fact: KeyFact, actions: list[CorporateAction], what: str) -> str | None:
    """Why a per-share figure cannot be set against today's price, or None when it can."""
    if fact.unit != "INR_PER_SHARE":
        return (
            f"Not assessable: the {what} is not in rupees, so it cannot be set against the price."
        )
    parsed = parse_period(fact.period)
    if parsed is None or parsed[1] != 0:
        return f"Not assessable: the {what} is not for a full year."
    year_end = date(parsed[0], 3, 31)
    after = [action for action in actions if action.trade_date > year_end]
    if after:
        first = min(action.trade_date for action in after)
        return (
            f"Not assessable: a bonus issue or split on {first.day} {first:%b %Y} came after "
            f"{fact.period}, so its per-share {what} no longer compares with today's price."
        )
    return None


def pe_ratio(
    latest: DailyPrice | None, eps: KeyFact | None, actions: list[CorporateAction]
) -> PriceValue:
    if latest is None:
        return _no("Not assessable: no share price is in the data.", eps)
    if eps is None:
        return _no("Not assessable: no yearly basic EPS is on record.")
    blocked = _blocked(eps, actions, "EPS")
    if blocked is not None:
        return _no(blocked, eps)
    if eps.value <= 0:
        return _no(f"Not assessable: basic EPS for {eps.period} is zero or negative.", eps)
    value = _one_decimal(latest.close / eps.value)
    reason = (
        f"Share price ₹{_plain(latest.close)} ({latest.trade_date:%d %b %Y}) divided by basic "
        f"EPS of ₹{_plain(eps.value)} for {eps.period} (latest full year on record)."
    )
    return PriceValue("ok", value, reason, eps)


def dividend_yield(
    latest: DailyPrice | None, dividend: KeyFact | None, actions: list[CorporateAction]
) -> PriceValue:
    if latest is None:
        return _no("Not assessable: no share price is in the data.", dividend)
    if dividend is None:
        return _no("Not assessable: no yearly dividend per share is on record.")
    blocked = _blocked(dividend, actions, "dividend")
    if blocked is not None:
        return _no(blocked, dividend)
    value = _one_decimal(dividend.value / latest.close * 100) if latest.close > 0 else None
    if value is None:
        return _no("Not assessable: the share price is zero.", dividend)
    reason = (
        f"Dividend of ₹{_plain(dividend.value)} per share for {dividend.period} (latest full "
        f"year on record) over the share price ₹{_plain(latest.close)} "
        f"({latest.trade_date:%d %b %Y})."
    )
    return PriceValue("ok", value, reason, dividend)
