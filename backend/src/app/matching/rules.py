"""Matching a stock to an investor profile with plain rules (P14; the project notes "Matching", rule 8).

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
                                                     (no prices: paid or not, not a yield)
    revenue_growth  growth                           revenue growth >= GROWTH_MIN 10%      soft
                                                     (a bank: net interest income growth)
    profit_growth   growth                           net profit growth >= 10%              soft
                    stability or conservative        net profit growth >= 0% (no fall)     soft
                    (several ask: the strictest)
    quality         quality                          latest full-year ROE >= 15%           soft
    value, momentum value, momentum                  not assessable: needs share prices
    horizon         long_term or short_term          not assessable: no price history
    sentiment       (always, as a caution)           rolling news sentiment is negative

Reasons come in Criterion order, one per criterion, each with its figure, its threshold and the
stored rows it comes from. Money is shown as reported, never converted.
"""

from datetime import date
from decimal import Decimal

from app.chat.evidence import _plain, format_amount
from app.derived import rolling_sentiment
from app.insights import (
    Citation,
    DerivedView,
    KeyFact,
    StoredEvent,
    derived_views,
    key_facts,
    recent_events,
)
from app.insights_store import StockRows
from app.matching.model import Criterion, Outcome, Reason, Status, StockMatch
from app.memory.vocabulary import StoredPreference

DEBT_LIMIT = Decimal("1.0")  # avoid_high_debt (hard)
CONSERVATIVE_DEBT_LIMIT = Decimal("0.5")  # conservative (soft)
GROWTH_MIN = Decimal("10")  # percent, a growth style
NO_FALL_MIN = Decimal("0")  # percent, stability or conservative
QUALITY_ROE_MIN = Decimal("15")  # percent, a quality style
CAUTION_CITATIONS = 3  # the most recent negative events cited

NO_PRICES = "needs share prices, which this app does not have"


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
    note = "Without share prices this judges whether a dividend is paid, not its yield."
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
    if "growth" not in wanted:
        return None
    view = _view(views, "revenue_growth")
    return _growth("revenue_growth", "growth", view, GROWTH_MIN, "wanted for a growth style")


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


# --- styles that cannot be judged -----------------------------------------------------------------


def _unassessable(criterion: Criterion, wanted: set[str]) -> Reason | None:
    if criterion not in wanted:
        return None
    return _reason(criterion, criterion, "not_assessable", f"{criterion.capitalize()} {NO_PRICES}.")


def _horizon(wanted: set[str]) -> Reason | None:
    for preference in ("long_term", "short_term"):
        if preference in wanted:
            text = "The app has no price history, so a horizon does not change the result."
            return _reason("horizon", preference, "not_assessable", text)
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
        _unassessable("value", wanted),
        _unassessable("momentum", wanted),
        _horizon(wanted),
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
