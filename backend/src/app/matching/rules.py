"""Matching a stock to an investor profile with plain rules (P14, ADR 023).

No LLM is involved: the profile is a fixed vocabulary (app/memory/vocabulary.py), the stock's
figures are stored facts (app/insights.py), and each preference becomes one rule below. The rules
live in code, not in a prompt or a table, so they are testable, the same every time, and a reader
can point at the exact line that produced a verdict.

Hard filters versus soft scores. A HARD rule is a dealbreaker the user asked for outright
("avoid high debt"): failing it makes the stock a "no match", and missing data for it means we
cannot say ("not enough data"). A SOFT rule is a leaning ("conservative", a growth style): missing
it only downgrades a "match" to "partial". Cautions (negative news) are shown beside the status
and never change it.

    criterion       asked by                         rule (figure from)                    hard?
    debt            avoid_high_debt                  debt to equity <= DEBT_LIMIT 1.0      hard
                    conservative                     debt to equity <= 0.5 (both: > 1.0    soft
                                                     fail, 0.5 to 1.0 miss, <= 0.5 pass)
    dividend        income                           latest dividend per share > 0         soft
                                                     (paid or not, not a yield)
    revenue_growth  growth, or aggressive            revenue growth >= GROWTH_MIN 10%      soft
                                                     (a bank: net interest income growth)
    profit_growth   growth                           net profit growth >= 10%              soft
                    stability or conservative        net profit growth >= 0% (no fall)     soft
                    (several ask: the strictest)
    quality         quality                          latest full-year ROE >= 15%           soft
    momentum        momentum                         PRICE momentum: six-month return >= 0% soft
                                                     (ADR 025); when prices do not reach back
                                                     six months, EARNINGS momentum: net profit
                                                     growth this year above last year's, three
                                                     years in a row from one source
    value           value                            P/E <= VALUE_PE_MAX 20 (latest close  soft
                                                     over the latest full-year basic EPS);
                                                     not assessable after a bonus or split
                                                     since that year, without prices or with
                                                     a loss; no EPS is no data
    horizon         short_term                       one-year volatility <=                soft
                                                     SHORT_TERM_VOL_MAX 30% (steadier for a
                                                     short holding); no data without a year
                    long_term                        net profit did not fall over the      soft
                                                     three-year chain (each year >= the
                                                     one before); no data without a chain
    sentiment       (always, as a caution)           rolling news sentiment is negative

Reasons come in Criterion order, one per criterion, each with its figure, its threshold and the
stored rows it comes from. Money is shown as reported, never converted.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.chat.evidence import _plain, format_amount
from app.derived import growth_chain, rolling_sentiment
from app.insights import (
    Citation,
    DerivedView,
    KeyFact,
    StoredEvent,
    StoredFact,
    derived_views,
    key_facts,
    recent_events,
)
from app.insights_store import StockRows
from app.matching.model import Criterion, Outcome, Reason, Status, StockMatch
from app.memory.vocabulary import StoredPreference
from app.prices.derived import (
    adjusted_closes,
    corporate_actions,
    latest_key_fact,
    pe_ratio,
    period_return,
    price_citation,
    volatility,
)
from app.prices.model import DailyPrice

DEBT_LIMIT = Decimal("1.0")  # avoid_high_debt (hard)
CONSERVATIVE_DEBT_LIMIT = Decimal("0.5")  # conservative (soft)
GROWTH_MIN = Decimal("10")  # percent, a growth style
NO_FALL_MIN = Decimal("0")  # percent, stability or conservative
QUALITY_ROE_MIN = Decimal("15")  # percent, a quality style
VALUE_PE_MAX = Decimal("20")  # price to earnings, a value style
SHORT_TERM_VOL_MAX = Decimal("30")  # percent a year, a short holding
MOMENTUM_MONTHS = 6
CAUTION_CITATIONS = 3  # the most recent negative events cited


def _reason(
    criterion: Criterion,
    preference: str,
    outcome: Outcome,
    text: str,
    citations: tuple[Citation, ...] = (),
    *,
    hard: bool = False,
) -> Reason:
    return Reason(criterion, preference, hard, outcome, text, citations)


def _view(views: list[DerivedView], name: str) -> DerivedView:
    return next(view for view in views if view.name == name)


def _judge(passed: bool) -> Outcome:
    return "pass" if passed else "miss"


def _percent(value: Decimal) -> str:
    return f"{_plain(value)}%"


# --- debt -----------------------------------------------------------------------------------------


def _debt(wanted: set[str], views: list[DerivedView]) -> Reason | None:
    hard = "avoid_high_debt" in wanted
    conservative = "conservative" in wanted
    if not (hard or conservative):
        return None
    preference = "avoid_high_debt" if hard else "conservative"
    view = _view(views, "debt_to_equity")
    if view.status == "not_applicable":
        return _reason("debt", preference, "not_assessable", view.reason, hard=hard)
    if view.status != "ok" or view.value is None:
        text = "Debt to equity is not in the data, so this cannot be judged."
        return _reason("debt", preference, "no_data", text, hard=hard)
    value, cites = view.value, view.citations
    figure = f"Debt to equity is {_plain(value)}"
    if hard and value > DEBT_LIMIT:
        text = f"{figure}, above the {DEBT_LIMIT} limit for avoiding high debt."
        return _reason("debt", preference, "fail", text, cites, hard=True)
    if not conservative:
        text = f"{figure}, within the {DEBT_LIMIT} limit for avoiding high debt."
        return _reason("debt", preference, "pass", text, cites, hard=True)
    limit = f"the {CONSERVATIVE_DEBT_LIMIT} a conservative investor prefers"
    if value <= CONSERVATIVE_DEBT_LIMIT:
        text = f"{figure}, within {limit}."
        return _reason("debt", preference, "pass", text, cites, hard=hard)
    if hard:
        text = f"{figure}: within the {DEBT_LIMIT} limit but above {limit}."
    else:
        text = f"{figure}, above {limit}."
    return _reason("debt", preference, "miss", text, cites, hard=hard)


# --- dividend -------------------------------------------------------------------------------------


def _dividend_text(value: Decimal, facts: list[KeyFact]) -> str:
    for fact in facts:
        if fact.metric == "dividend_per_share" and fact.value == value:
            return format_amount(value, fact.unit)
    return f"{_plain(value)} per share"


def _dividend(wanted: set[str], views: list[DerivedView], facts: list[KeyFact]) -> Reason | None:
    if "income" not in wanted:
        return None
    view = _view(views, "latest_dividend")
    if view.status != "ok" or view.value is None:
        text = "No dividend per share is in the data, so this cannot be judged."
        return _reason("dividend", "income", "no_data", text)
    shown = _dividend_text(view.value, facts)
    note = "This judges whether a dividend is paid, not its yield."
    if view.value > 0:
        text = f"The latest dividend is {shown}, so the company pays one. {note}"
    else:
        text = f"The latest dividend is {shown}, so the company pays none. {note}"
    return _reason("dividend", "income", _judge(view.value > 0), text, view.citations)


# --- growth ---------------------------------------------------------------------------------------


def _growth(
    criterion: Criterion, preference: str, view: DerivedView, minimum: Decimal, need: str
) -> Reason:
    if view.status != "ok" or view.value is None:
        text = f"{view.label} is not in the data, so this cannot be judged."
        return _reason(criterion, preference, "no_data", text)
    value = view.value
    if value >= minimum:
        text = f"{view.label} is {_percent(value)}, at or above the {_percent(minimum)} {need}."
    else:
        text = f"{view.label} is {_percent(value)}, below the {_percent(minimum)} {need}."
    return _reason(criterion, preference, _judge(value >= minimum), text, view.citations)


def _revenue_growth(wanted: set[str], views: list[DerivedView]) -> Reason | None:
    view = _view(views, "revenue_growth")
    if "growth" in wanted:
        return _growth("revenue_growth", "growth", view, GROWTH_MIN, "wanted for a growth style")
    if "aggressive" in wanted:
        need = "wanted for an aggressive investor"
        return _growth("revenue_growth", "aggressive", view, GROWTH_MIN, need)
    return None


def _profit_growth(wanted: set[str], views: list[DerivedView]) -> Reason | None:
    view = _view(views, "profit_growth")
    if "growth" in wanted:
        return _growth("profit_growth", "growth", view, GROWTH_MIN, "wanted for a growth style")
    for preference in ("stability", "conservative"):
        if preference in wanted:
            return _growth("profit_growth", preference, view, NO_FALL_MIN, "floor (no fall) wanted")
    return None


# --- quality --------------------------------------------------------------------------------------


def _quality(wanted: set[str], facts: list[KeyFact]) -> Reason | None:
    if "quality" not in wanted:
        return None
    roe = next((fact for fact in facts if fact.metric == "return_on_equity"), None)
    if roe is None:
        text = "Return on equity is not in the data, so this cannot be judged."
        return _reason("quality", "quality", "no_data", text)
    shown = format_amount(roe.value, roe.unit)
    need = f"{_percent(QUALITY_ROE_MIN)} wanted for a quality style"
    if roe.value >= QUALITY_ROE_MIN:
        text = f"Return on equity for {roe.period} is {shown}, at or above the {need}."
    else:
        text = f"Return on equity for {roe.period} is {shown}, below the {need}."
    return _reason(
        "quality", "quality", _judge(roe.value >= QUALITY_ROE_MIN), text, (roe.citation,)
    )


# --- momentum: price when six months of prices exist, else earnings -------------------------------


def _rate(before: Decimal, after: Decimal) -> Decimal:
    return ((after - before) / before * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _price_momentum(share_prices: list[DailyPrice]) -> Reason | None:
    """The six-month return of the adjusted close; None when the prices do not reach back."""
    if not share_prices:
        return None
    latest = share_prices[-1].trade_date
    change = period_return(adjusted_closes(share_prices), months=MOMENTUM_MONTHS, as_of=latest)
    if change is None:
        return None
    direction = "up" if change >= 0 else "down"
    text = (
        f"Share price is {direction} {_percent(abs(change))} over six months "
        f"(to {latest:%d %b %Y}), from BSE's daily price files."
    )
    return _reason("momentum", "momentum", _judge(change >= 0), text, (price_citation(latest),))


def _earnings_momentum(facts: list[StoredFact]) -> Reason:
    """Net profit growth this year faster than the year before, from three years in a row of one
    source (derived.growth_chain): the fallback when prices do not reach back six months."""
    chain = growth_chain([stored.row for stored in facts], "net_profit")
    if chain is None:
        text = (
            "Momentum needs six months of share prices or three years of net profit in a row "
            "from one source, which are not in the data."
        )
        return _reason("momentum", "momentum", "no_data", text)
    first, middle, latest = chain
    citations = {stored.row.id: stored.citation for stored in facts}
    cites = tuple(citations[row.id] for row in chain)
    if first.value <= 0 or middle.value <= 0:
        text = "Net profit was zero or negative in one of the years, so growth rates mean nothing."
        return _reason("momentum", "momentum", "no_data", text, cites)
    before, after = _rate(first.value, middle.value), _rate(middle.value, latest.value)
    speeding = after > before
    text = (
        f"Net profit changed by {_percent(after)} in {latest.period} against "
        f"{_percent(before)} in {middle.period}: earnings are "
        f"{'speeding up' if speeding else 'not speeding up'} (earnings momentum, used because "
        "six months of share prices are not in the data)."
    )
    return _reason("momentum", "momentum", _judge(speeding), text, cites)


def _momentum(
    wanted: set[str], facts: list[StoredFact], share_prices: list[DailyPrice]
) -> Reason | None:
    if "momentum" not in wanted:
        return None
    return _price_momentum(share_prices) or _earnings_momentum(facts)


# --- value: price to earnings ---------------------------------------------------------------------


def _value(wanted: set[str], share_prices: list[DailyPrice], facts: list[KeyFact]) -> Reason | None:
    if "value" not in wanted:
        return None
    if not share_prices:
        text = "Value needs share prices, which are not in the data yet."
        return _reason("value", "value", "not_assessable", text)
    latest = share_prices[-1]
    basic_eps = latest_key_fact(facts, "eps_basic")
    result = pe_ratio(latest, basic_eps, corporate_actions(share_prices))
    cites = (price_citation(latest.trade_date),) + ((basic_eps.citation,) if basic_eps else ())
    if basic_eps is None:
        text = "Price to earnings is not in the data: no yearly basic EPS is on record."
        return _reason("value", "value", "no_data", text, cites[:1])
    if result.value is None:
        return _reason("value", "value", "not_assessable", result.reason, cites)
    limit = f"the {_plain(VALUE_PE_MAX)} wanted for a value style"
    relation = "at or below" if result.value <= VALUE_PE_MAX else "above"
    text = f"Price to earnings is {_plain(result.value)}, {relation} {limit}. {result.reason}"
    return _reason("value", "value", _judge(result.value <= VALUE_PE_MAX), text, cites)


# --- horizon: steady prices for a short holding, steady earnings for a long one -------------------


def _short_term(share_prices: list[DailyPrice]) -> Reason:
    swing = volatility(adjusted_closes(share_prices))
    if swing is None:
        text = "A short holding is judged on a year of share price swings, not in the data."
        return _reason("horizon", "short_term", "no_data", text)
    cites = (price_citation(share_prices[-1].trade_date),)
    relation = "at or below" if swing <= SHORT_TERM_VOL_MAX else "above"
    text = (
        f"Share price swings over the last year are {_percent(swing)} a year, {relation} the "
        f"{_percent(SHORT_TERM_VOL_MAX)} that suits a short holding."
    )
    return _reason("horizon", "short_term", _judge(swing <= SHORT_TERM_VOL_MAX), text, cites)


def _long_term(facts: list[StoredFact]) -> Reason:
    chain = growth_chain([stored.row for stored in facts], "net_profit")
    if chain is None:
        text = (
            "A long holding is judged on net profit over three years in a row from one source, "
            "which is not in the data."
        )
        return _reason("horizon", "long_term", "no_data", text)
    citations = {stored.row.id: stored.citation for stored in facts}
    cites = tuple(citations[row.id] for row in chain)
    steady = chain[0].value <= chain[1].value <= chain[2].value
    years = ", ".join(f"{row.period} {format_amount(row.value, row.unit)}" for row in chain)
    outcome = "did not fall" if steady else "fell in at least one year"
    return _reason(
        "horizon",
        "long_term",
        _judge(steady),
        f"Net profit {outcome} over three years ({years}).",
        cites,
    )


def _horizon(
    wanted: set[str], share_prices: list[DailyPrice], facts: list[StoredFact]
) -> Reason | None:
    if "long_term" in wanted:
        return _long_term(facts)
    if "short_term" in wanted:
        return _short_term(share_prices)
    return None


# --- cautions and status --------------------------------------------------------------------------


def _caution(events: list[StoredEvent], today: date) -> tuple[Reason, ...]:
    sentiment = rolling_sentiment([event.row for event in events], as_of=today)
    if sentiment.label != "negative" or sentiment.score is None:
        return ()
    negative = [
        event
        for event in recent_events(events, limit=len(events))
        if event.row.sentiment == "negative"
    ]
    cites = tuple(event.citation for event in negative[:CAUTION_CITATIONS])
    text = (
        f"Recent news sentiment is negative (score {sentiment.score} over "
        f"{len(sentiment.event_ids)} events in the last year)."
    )
    return (_reason("sentiment", "", "miss", text, cites),)


def _status(reasons: tuple[Reason, ...]) -> Status:
    outcomes = [reason.outcome for reason in reasons]
    if "fail" in outcomes:
        return "no_match"
    if any(reason.hard and reason.outcome == "no_data" for reason in reasons):
        return "not_enough_data"
    judged = [outcome for outcome in outcomes if outcome in ("pass", "miss")]
    if not judged:
        return "not_enough_data"
    # A full match needs every criterion that can be judged here checked and met: a soft one
    # with no data leaves it partial.
    checked = [outcome for outcome in outcomes if outcome != "not_assessable"]
    return "match" if all(outcome == "pass" for outcome in checked) else "partial"


def match_stock(profile: list[StoredPreference], stock: StockRows, *, today: date) -> StockMatch:
    wanted = {value for preference in profile for value in preference.values}
    facts = key_facts(stock.facts)
    views = derived_views(stock.facts, is_financial=stock.is_financial)
    candidates = (
        _debt(wanted, views),
        _dividend(wanted, views, facts),
        _revenue_growth(wanted, views),
        _profit_growth(wanted, views),
        _quality(wanted, facts),
        _value(wanted, stock.prices, facts),
        _momentum(wanted, stock.facts, stock.prices),
        _horizon(wanted, stock.prices, stock.facts),
    )
    reasons = tuple(reason for reason in candidates if reason is not None)
    if not profile:
        return StockMatch(stock.symbol, stock.name, "not_enough_data", (), ())
    return StockMatch(
        stock.symbol, stock.name, _status(reasons), reasons, _caution(stock.events, today)
    )


def match_all(
    profile: list[StoredPreference], stocks: list[StockRows], *, today: date
) -> list[StockMatch]:
    return [match_stock(profile, stock, today=today) for stock in stocks]
