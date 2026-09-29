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
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal, TypeVar

from app.chat.understand import Question
from app.derived import Sentiment
from app.extraction_prompts import document_block
from app.insights import DerivedView, KeyFact, StoredEvent, filing_citation, recent_events
from app.matching.model import StockMatch
from app.retrieval import Result, excerpt

PASSAGE_CHARS = 600  # the model reads this much of a passage; the source shows EXCERPT_CHARS

GROWTH = ("revenue_growth", "profit_growth")
ALL_DERIVED = ("debt_to_equity", *GROWTH, "latest_dividend")

T = TypeVar("T")


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


def _fact_item(number: int, symbol: str, fact: KeyFact) -> EvidenceItem:
    text = f"{symbol} · {fact.label} · {fact.period} · {fact.basis} · "
    text += format_amount(fact.value, fact.unit)
    text += f" · {fact.citation.label.split(' · ')[0]}"  # "Annual report", "screener.in"
    if fact.status == "disputed":
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
    )


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


def _derived_item(
    number: int, symbol: str, view: DerivedView, facts: list[KeyFact]
) -> EvidenceItem:
    if view.status == "ok" and view.value is not None:
        shown = _derived_value(view, view.value, facts)
    else:
        shown = view.status.replace("_", " ")  # "not applicable": no number
    return EvidenceItem(
        id=f"D{number}",
        kind="derived",
        symbol=symbol,
        text=f"{symbol} · {view.label} · {shown} · {view.reason}",
        source="derived",
        label=view.label,  # the page marks it "Computed"
        url=None,
        quote=view.reason,
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

    derived_items = [
        *(_derived_item(0, symbol, v, facts.get(symbol, [])) for symbol, v in chosen_views),
        *(_sentiment_item(0, symbol, mood) for symbol, mood in moods),
    ]
    return [
        *(_fact_item(n, symbol, f) for n, (symbol, f) in enumerate(chosen_facts, start=1)),
        *(replace(item, id=f"D{n}") for n, item in enumerate(derived_items, start=1)),
        *(_match_item(n, m) for n, m in enumerate(chosen_matches, start=1)),
        *(_passage_item(n, r) for n, r in enumerate(chosen_passages, start=1)),
        *(_event_item(n, symbol, e) for n, (symbol, e) in enumerate(chosen_events, start=1)),
    ]


def evidence_block(items: list[EvidenceItem]) -> str:
    """What the model reads: "[F1] ..." one per line; the passages inside <document> tags."""
    untrusted = ("passage", "event")
    ours = [f"[{item.id}] {item.text}" for item in items if item.kind not in untrusted]
    theirs = [f"[{item.id}] {item.text}" for item in items if item.kind in untrusted]
    if theirs:
        ours.append(document_block("\n".join(theirs)))
    return "\n".join(ours)
