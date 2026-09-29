"""The numbered evidence the chat model may cite (P12; the project notes "RAG and citations").

Everything the model is shown is an item with an ID, and an answer may only cite those IDs:

    F1, F2 ...  facts: one stored figure each, already chosen by the owner's conflict policy
                (app/insights.py's key_facts), cited to its filing page or screener.in row
    D1, D2 ...  derived values computed on read (debt to equity, growth, latest dividend),
                cited as "Computed: ..." with the sentence that says how
    N1, N2 ...  passages of the filings found by search (app/retrieval.py), cited to the page
    M1, M2 ...  for "Match me" questions (P14): the verdict per stock that app/matching/rules.py
                computed from the investor's profile, with its reasons and figures; the model
                only explains them
    E1, E2 ...  events from the filings (P11), newest first, cited to the filing page; only when
                the question asks about news or events, together with a D item per stock for
                the rolling news sentiment (app/derived.py, computed on read)

Each item's ``text`` is one line: what the model reads AND what app/chat/answer_check.py
checks the answer's numbers against. So a number is written the way a reader would write it:
"₹1,23,456 crore" (Indian grouping for rupees), "US$1,800 million" (dollars as reported, never
converted), "₹5.5 per share", "14.7%", with the stored 4-decimal value's trailing zeros dropped.

What is kept:

- only the question's stocks; facts of the metrics asked (every metric when none was asked);
  at most ``max_facts``, taken one per stock in turn so that a cap cannot starve the last stock;
- derived values: the two growth ones when growth is asked, debt to equity when debt is asked,
  the latest dividend when the dividend is asked, all four when nothing specific is asked. A
  value that is not "ok" is still given (its reason lets the answer say "not applicable for
  banks"), but with no number;
- passages in the order search ranked them, at most ``max_passages``;
- events, newest first, at most ``max_events``, one stock at a time in turn.

Passages are text from the filings and therefore untrusted: a filing could contain "ignore your
instructions". An event's summary was written by the extraction model from such text, so it is
untrusted too. ``evidence_block`` wraps both in <document> tags the text cannot close
(app/extraction_prompts.py's document_block). Facts and derived values are our own lines.

One figure per thing (the owner's review, 2026-09-29):

- measures_for picks each asked measure's figures from one source (app/insights.py's
  measure_facts) and the changes between them (change_views), so the facts and the changes of
  an answer rest on the same stored figures;
- each F and D item lists the stored figures it rests on (``figures``); the checker refuses an
  answer that rests on two different figures for one stock, measure, period and basis, and the
  table is shown only if it agrees with them;
- another source's differing figure is its own F item (``rivals`` and ``rival_of`` link them), so
  the answer can disclose it with its own source instead of choosing silently; the two may be cited
  together (that is the disclosure), but a rival never stands in for the figure in a change;
- a computed value says which stored figures it used ("Figures used: ...").

A worked example (Reliance's revenue, the owner's real case): the annual reports give FY2025
₹9,80,136 crore and FY2024 ₹9,14,472 crore; screener.in gives FY2026 ₹10,55,780 crore, FY2025
₹9,62,820 crore and FY2024 ₹8,99,041 crore (it counts revenue a little differently).

- "Compare FY2024 and FY2025": the annual reports have both years, so F1 = FY2025 ₹9,80,136 crore
  and F3 = FY2024 ₹9,14,472 crore; F2 and F4 are screener.in's figures for those years, marked as
  another source's; D1 = the change 7.2%, "Figures used: ₹9,14,472 crore (FY2024), ₹9,80,136
  crore (FY2025)" -- the same figures as F1 and F3.
- "The last three years": the annual reports lack FY2026, so all three years are screener.in's,
  the annual reports' FY2025 and FY2024 become the other source's items, and the changes (9.7%,
  7.1%) are computed from screener.in's figures -- the same ones its table shows.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal, NamedTuple, TypeVar

from app.chat.understand import Question
from app.derived import FactRow, Sentiment
from app.extraction_prompts import document_block
from app.insights import (
    YEARS_SHOWN,
    DerivedView,
    KeyFact,
    Rival,
    StoredEvent,
    StoredFact,
    change_views,
    filing_citation,
    measure_facts,
    recent_events,
)
from app.matching.model import StockMatch
from app.prices.derived import PriceSnapshot, PriceValue, price_citation
from app.prices.model import PRICE_SOURCE_LABEL, DailyPrice
from app.retrieval import Result, excerpt
from app.vocabulary import METRICS

PASSAGE_CHARS = 600  # the model reads this much of a passage; the source shows EXCERPT_CHARS

GROWTH = ("revenue_growth", "profit_growth", "change")
ALL_DERIVED = ("debt_to_equity", *GROWTH, "latest_dividend")

T = TypeVar("T")


class Figure(NamedTuple):
    """One stored figure an item rests on."""

    symbol: str
    metric: str
    period: str
    basis: str
    value: Decimal


@dataclass(frozen=True)
class EvidenceItem:
    id: str  # "F1", "D1", "M1", "N1", "E1"
    kind: Literal["fact", "derived", "match", "passage", "event"]
    symbol: str
    text: str  # one line: what the model reads and what the checker verifies numbers against
    source: Literal["filing", "screener", "rbi", "derived"]
    label: str
    url: str | None
    quote: str | None
    metric: str | None = None  # facts only: what and when, for the mixed-sources check
    period: str | None = None
    figures: tuple[Figure, ...] = ()  # the stored figures it rests on (F and D items)
    amount: str | None = None  # facts: the figure as written, "₹1,234 crore"
    rivals: tuple[str, ...] = ()  # facts: the IDs of other sources' differing figures
    rival_of: str | None = None  # another source's figure: the ID of the fact it differs from
    reported_as: str | None = None  # facts: what the source calls the figure


# --- writing numbers ------------------------------------------------------------------------------


def _plain(value: Decimal) -> str:
    """ "1234.5000" -> "1234.5", "10.0000" -> "10": the stored value without trailing zeros."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _indian(digits: str) -> str:
    """ "12345678.5" -> "1,23,45,678.5": the last three digits, then pairs."""
    whole, dot, fraction = digits.partition(".")
    head, tail = whole[:-3], whole[-3:]
    pairs: list[str] = []
    while head:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([*pairs, tail]) + dot + fraction


def _western(digits: str) -> str:
    """ "1800.5" -> "1,800.5": groups of three."""
    whole, dot, fraction = digits.partition(".")
    return f"{int(whole):,}" + dot + fraction


def format_amount(value: Decimal, unit: str) -> str:
    """A stored value as a reader writes it, in its own currency (never converted)."""
    sign = "-" if value < 0 else ""
    digits = _plain(abs(value))
    if unit == "INR_CRORE":
        return f"{sign}₹{_indian(digits)} crore"
    if unit == "USD_MILLION":
        return f"{sign}US${_western(digits)} million"
    if unit == "INR_PER_SHARE":
        return f"{sign}₹{digits} per share"
    if unit == "USD_PER_SHARE":
        return f"{sign}US${digits} per share"
    if unit == "PERCENT":
        return f"{sign}{digits}%"
    return f"{sign}{digits} {unit}"


# --- choosing -------------------------------------------------------------------------------------


def _in_turn(groups: list[list[T]], limit: int) -> list[T]:
    """At most ``limit`` items, taken one from each group in turn, returned group by group."""
    counts = [0] * len(groups)
    taken = 0
    depth = 0
    while taken < limit and any(depth < len(group) for group in groups):
        for i, group in enumerate(groups):
            if depth < len(group) and taken < limit:
                counts[i] += 1
                taken += 1
        depth += 1
    return [item for group, count in zip(groups, counts, strict=True) for item in group[:count]]


def _derived_wanted(question: Question) -> set[str]:
    if not question.metrics and not question.wants_growth:
        return set(ALL_DERIVED)
    wanted = set(GROWTH) if question.wants_growth else set()
    if "total_borrowings" in question.metrics:
        wanted.add("debt_to_equity")
    if "dividend_per_share" in question.metrics:
        wanted.add("latest_dividend")
    return wanted


# --- the items ------------------------------------------------------------------------------------


def source_word(label: str) -> str:
    """ "Annual report", "screener.in", "Earnings call": the first part of a citation label."""
    return label.split(" · ")[0]


def _as_reported(label: str | None) -> str:
    return f', reported as "{label}"' if label else ""


def _fact_item(number: int, symbol: str, fact: KeyFact) -> EvidenceItem:
    amount = format_amount(fact.value, fact.unit)
    text = f"{symbol} · {fact.label} · {fact.period} · {fact.basis} · {amount}"
    text += f" · {source_word(fact.citation.label)}{_as_reported(fact.reported_as)}"
    if fact.status == "disputed" and not fact.rivals:  # with rivals, they are listed instead
        text += " (disputed: another source differs)"
    return EvidenceItem(
        id=f"F{number}",
        kind="fact",
        symbol=symbol,
        text=text,
        source=fact.citation.source,
        label=fact.citation.label,
        url=fact.citation.url,
        quote=fact.citation.quote,
        metric=fact.metric,
        period=fact.period,
        figures=(Figure(symbol, fact.metric, fact.period, fact.basis, fact.value),),
        amount=amount,
        reported_as=fact.reported_as,
    )


def _rival_item(symbol: str, fact: KeyFact, rival: Rival) -> EvidenceItem:
    """Another source's differing figure for the same thing, cited when the answer discloses it."""
    amount = format_amount(rival.value, rival.unit)
    text = f"{symbol} · {fact.label} · {fact.period} · {fact.basis} · {amount}"
    return EvidenceItem(
        id="F0",  # numbered, and linked to its fact, by _numbered_facts
        kind="fact",
        symbol=symbol,
        text=f"{text} · {source_word(rival.citation.label)}{_as_reported(rival.reported_as)}",
        source=rival.citation.source,
        label=rival.citation.label,
        url=rival.citation.url,
        quote=rival.citation.quote,
        metric=fact.metric,
        period=fact.period,
        figures=(Figure(symbol, fact.metric, fact.period, fact.basis, rival.value),),
        amount=amount,
        reported_as=rival.reported_as,
    )


def _numbered_facts(
    entries: list[tuple[EvidenceItem, list[EvidenceItem]]],
) -> list[EvidenceItem]:
    """F1, F2 ... in order, each fact followed by its rivals, linked both ways."""
    items: list[EvidenceItem] = []
    for main, rivals in entries:
        main_id = f"F{len(items) + 1}"
        rival_ids = tuple(f"F{len(items) + 2 + i}" for i in range(len(rivals)))
        text = main.text + (f" (another source differs: {', '.join(rival_ids)})" if rivals else "")
        items.append(replace(main, id=main_id, text=text, rivals=rival_ids))
        note = f" (another source's figure for the same period; {main_id} is used)"
        items += [
            replace(rival, id=rid, text=rival.text + note, rival_of=main_id)
            for rival, rid in zip(rivals, rival_ids, strict=True)
        ]
    return items


def _derived_value(view: DerivedView, value: Decimal, facts: list[KeyFact]) -> str:
    """Growth as a percent, debt to equity plain, the dividend per share in the currency of the
    dividend fact it is (a derived value carries no unit of its own)."""
    if view.name in GROWTH:
        return f"{_plain(value)}%"
    if view.name == "latest_dividend":
        for fact in facts:
            if fact.metric == "dividend_per_share" and fact.value == value:
                return format_amount(value, fact.unit)
        return f"{_plain(value)} per share"
    return _plain(value)


def _figures_used(inputs: tuple[FactRow, ...]) -> str:
    """ "Figures used: ₹962 crore (FY2025), ₹1,055 crore (FY2026)." Each figure is named by its
    metric when the inputs are of more than one (total borrowings and total equity)."""
    named = len({row.metric for row in inputs}) > 1
    parts = [
        (f"{row.metric.replace('_', ' ')} " if named else "")
        + f"{format_amount(row.value, row.unit)} ({row.period})"
        for row in inputs
    ]
    return f"Figures used: {', '.join(parts)}."


def _calculation(inputs: tuple[FactRow, ...], percent: Decimal, difference: Decimal) -> str:
    """How a change was worked out, in the figures' own unit, for example "Calculation:
    (₹1,055 crore - ₹962 crore) / ₹962 crore x 100 = 9.7%; up ₹93 crore." """
    earlier, later = (format_amount(row.value, row.unit) for row in inputs)
    moved = "up" if difference >= 0 else "down"
    size = format_amount(abs(difference), inputs[0].unit)
    return (
        f"Calculation: ({later} - {earlier}) / {earlier} x 100 = {_plain(percent)}%; "
        f"{moved} {size}."
    )


def _derived_item(
    number: int, symbol: str, view: DerivedView, facts: list[KeyFact]
) -> EvidenceItem:
    if view.status == "ok" and view.value is not None:
        shown = _derived_value(view, view.value, facts)
    else:
        shown = view.status.replace("_", " ")  # "not applicable": no number
    how = f"{view.reason} {_figures_used(view.inputs)}" if view.inputs else view.reason
    if view.name == "change" and view.value is not None and view.difference is not None:
        how += f" {_calculation(view.inputs, view.value, view.difference)}"
    return EvidenceItem(
        id=f"D{number}",
        kind="derived",
        symbol=symbol,
        text=f"{symbol} · {view.label} · {shown} · {how}",
        source="derived",
        label=view.label,  # the page marks it "Computed"
        url=None,
        quote=how,
        figures=tuple(
            Figure(symbol, row.metric, row.period, row.basis, row.value) for row in view.inputs
        ),
    )


def _passage_item(number: int, result: Result) -> EvidenceItem:
    p = result.passage
    citation = filing_citation(p.kind, p.period, p.title, p.source_url, p.page, excerpt(p.text))
    return EvidenceItem(
        id=f"N{number}",
        kind="passage",
        symbol=p.symbol,
        text=f"{p.symbol} · {citation.label}: {excerpt(p.text, PASSAGE_CHARS)}",
        source="filing",
        label=citation.label,
        url=citation.url,
        quote=citation.quote,
    )


def _sentiment_item(number: int, symbol: str, sentiment: Sentiment) -> EvidenceItem:
    if sentiment.status == "ok":
        count = len(sentiment.event_ids)
        shown = f"{sentiment.label} (score {sentiment.score}, from {count} events in the last year)"
    else:
        shown = "not enough recent events to judge"  # no number: nothing to cite as a figure
    return EvidenceItem(
        id=f"D{number}",
        kind="derived",
        symbol=symbol,
        text=f"{symbol} · News sentiment · {shown}",
        source="derived",
        label="News sentiment",
        url=None,
        quote="Events of the last year, weighted by impact and halved every 90 days.",
    )


def _event_item(number: int, symbol: str, event: StoredEvent) -> EvidenceItem:
    row = event.row
    kind = row.event_type.replace("_", " ")
    text = f"{symbol} · {row.event_date:%d %b %Y} · {kind} · {row.sentiment} · "
    text += f"{row.impact} impact · {event.summary}"
    return EvidenceItem(
        id=f"E{number}",
        kind="event",
        symbol=symbol,
        text=text,
        source=event.citation.source,  # a filing, or an RBI press release (P15)
        label=event.citation.label,
        url=event.citation.url,
        quote=event.citation.quote,
    )


# --- share prices (ADR 025) -----------------------------------------------------------------------

_RETURN_WORDS = {"1m": "1 month", "3m": "3 months", "6m": "6 months", "1y": "1 year"}
_NO_HISTORY = "not enough history"


def _price_fact(symbol: str, day: DailyPrice, change: Decimal | None) -> EvidenceItem:
    """The latest close, a stored figure cited to that day's BSE file (numbered with the facts)."""
    citation = price_citation(day.trade_date)
    close = format_amount(day.close, "INR_PER_SHARE")
    previous = format_amount(day.prev_close, "INR_PER_SHARE")
    text = (
        f"{symbol} · Share price · {day.trade_date:%d %b %Y} · close {close}, previous close "
        f"{previous}, change {_plain(change or Decimal(0))}% · {PRICE_SOURCE_LABEL}"
    )
    return EvidenceItem(
        id="F0",
        kind="fact",
        symbol=symbol,
        text=text,
        source="filing",
        label=citation.label,
        url=citation.url,
        quote=None,
        metric="share_price",
        period=day.trade_date.isoformat(),
    )


def _computed(symbol: str, label: str, shown: str, reason: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        id="D0",
        kind="derived",
        symbol=symbol,
        text=f"{symbol} · {label} · {shown}" + (f" · {reason}" if reason else ""),
        source="derived",
        label=label,
        url=None,
        quote=reason,
        amount=shown,
    )


def _ratio(symbol: str, label: str, value: PriceValue, unit: str = "") -> EvidenceItem:
    if value.status == "ok" and value.value is not None:
        return _computed(symbol, label, f"{_plain(value.value)}{unit}", value.reason)
    return _computed(symbol, label, "not assessable", value.reason)


def _price_views(symbol: str, snap: PriceSnapshot) -> list[EvidenceItem]:
    if snap.latest is None:
        shown = "not in the data yet (end-of-day prices from BSE's daily files)"
        return [_computed(symbol, "Share price", shown)]
    returns = " · ".join(
        f"{_RETURN_WORDS[key]} {_plain(value)}%"
        if value is not None
        else f"{_RETURN_WORDS[key]} {_NO_HISTORY}"
        for key, value in snap.returns.items()
    )
    volatility = f"{_plain(snap.volatility)}%" if snap.volatility is not None else _NO_HISTORY
    return [
        _computed(symbol, "Share price returns, adjusted for bonus issues and splits", returns),
        _computed(symbol, "One-year share price volatility", volatility),
        _ratio(symbol, "Price to earnings", snap.pe),
        _ratio(symbol, "Dividend yield", snap.dividend_yield, "%"),
    ]


_STATUS_WORDS = {
    "match": "match",
    "partial": "partial match",
    "no_match": "no match",
    "not_enough_data": "not enough data",
}


def _match_item(number: int, match: StockMatch) -> EvidenceItem:
    reasons = " ".join(reason.text for reason in match.reasons)
    cautions = " ".join(f"Caution: {caution.text}" for caution in match.cautions)
    shown = " ".join(part for part in (reasons, cautions) if part)
    text = f"{match.symbol} · Match for your profile: {_STATUS_WORDS[match.status]}"
    return EvidenceItem(
        id=f"M{number}",
        kind="match",
        symbol=match.symbol,
        text=f"{text} · {shown}" if shown else text,
        source="derived",
        label="Match for your profile",
        url=None,
        quote=shown or None,
    )


def build_evidence(
    *,
    question: Question,
    facts: dict[str, list[KeyFact]],
    derived: dict[str, list[DerivedView]],
    passages: list[Result],
    events: dict[str, list[StoredEvent]] | None = None,
    sentiment: dict[str, Sentiment] | None = None,
    matches: list[StockMatch] | None = None,
    prices: dict[str, PriceSnapshot] | None = None,
    max_facts: int = 30,
    max_passages: int = 8,
    max_events: int = 10,
) -> list[EvidenceItem]:
    """Facts, derived values, matches, passages, events, in that order, each numbered from 1."""
    asked = set(question.metrics)
    fact_groups = [
        [(symbol, f) for f in facts.get(symbol, []) if not asked or f.metric in asked]
        for symbol in question.symbols
    ]
    chosen_facts = _in_turn(fact_groups, max_facts)

    wanted = _derived_wanted(question)
    chosen_views = [
        (symbol, v)
        for symbol in question.symbols
        for v in derived.get(symbol, [])
        if v.name in wanted
    ]
    chosen_passages = [r for r in passages if r.passage.symbol in question.symbols][:max_passages]

    chosen_matches = [m for m in matches or [] if m.symbol in question.symbols]

    news = question.wants_events
    moods = [(s, (sentiment or {})[s]) for s in question.symbols if news and s in (sentiment or {})]
    event_groups = [
        [(symbol, e) for e in recent_events((events or {}).get(symbol, []), limit=max_events)]
        for symbol in question.symbols
    ]
    chosen_events = _in_turn(event_groups, max_events) if news else []

    snaps = [
        (s, (prices or {})[s])
        for s in question.symbols
        if question.wants_price and s in (prices or {})
    ]
    fact_entries = [
        *(
            (_fact_item(0, symbol, f), [_rival_item(symbol, f, rival) for rival in f.rivals])
            for symbol, f in chosen_facts
        ),
        *(
            (_price_fact(symbol, snap.latest, snap.day_change), [])
            for symbol, snap in snaps
            if snap.latest is not None
        ),
    ]
    derived_items = [
        *(_derived_item(0, symbol, v, facts.get(symbol, [])) for symbol, v in chosen_views),
        *(_sentiment_item(0, symbol, mood) for symbol, mood in moods),
        *(item for symbol, snap in snaps for item in _price_views(symbol, snap)),
    ]
    return [
        *_numbered_facts(fact_entries),
        *(replace(item, id=f"D{n}") for n, item in enumerate(derived_items, start=1)),
        *(_match_item(n, m) for n, m in enumerate(chosen_matches, start=1)),
        *(_passage_item(n, r) for n, r in enumerate(chosen_passages, start=1)),
        *(_event_item(n, symbol, e) for n, (symbol, e) in enumerate(chosen_events, start=1)),
    ]


def measures_for(
    question: Question, facts: list[StoredFact], *, is_financial: bool
) -> tuple[list[KeyFact], list[DerivedView]]:
    """One stock's figures for the question and the changes between them. Each measure asked
    (every metric when none is) comes from one source (app/insights.py's measure_facts): the
    periods the question names, else its window of years (the latest three by default, at
    least two when a change is wanted). A measure a stock lacks is simply absent (never another
    in its place: code then says it is not available). With no measure asked, only the latest
    change of the top line (revenue, or net interest income for a bank) and of net profit is
    kept."""
    asked = question.metrics
    years = question.years or YEARS_SHOWN
    if question.wants_growth or not asked:
        years = max(years, 2)
    top_line = "net_interest_income" if is_financial else "revenue_from_operations"
    shown: list[KeyFact] = []
    changes: list[DerivedView] = []
    for metric in asked or METRICS:
        picked = measure_facts(
            facts, metric, periods=question.periods, years=years, basis=question.basis
        )
        shown += picked
        if asked:
            changes += change_views(picked)
        elif metric in (top_line, "net_profit"):
            changes += change_views(picked)[:1]
    return shown, changes


def evidence_block(items: list[EvidenceItem]) -> str:
    """What the model reads: "[F1] ..." one per line; the passages inside <document> tags."""
    untrusted = ("passage", "event")
    ours = [f"[{item.id}] {item.text}" for item in items if item.kind not in untrusted]
    theirs = [f"[{item.id}] {item.text}" for item in items if item.kind in untrusted]
    if theirs:
        ours.append(document_block("\n".join(theirs)))
    return "\n".join(ours)
