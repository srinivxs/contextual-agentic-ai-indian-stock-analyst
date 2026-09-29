"""Answers written by code, without the model (the owner's second review, 2026-09-29).

Each is a plain function over what the workflow already holds, so it is tested on its own:

- valuation_claims: "Is TCS undervalued?" gets each stock's stored price-to-earnings ratio (a
  computed D item) and then VALUATION_GAP_TEXT: what a verdict would need that the data does not
  hold. Only what is known to be missing is named: no other company's or long-run multiples (one
  month of prices is stored), no earnings forecasts.
- source_claims: "Where did you get that?" lists the sources of the previous answer, each with
  its marker, from the stored answer itself (the model is not asked to remember).
- explained: whether a "why" answer gives a cause (because, due to, driven by ...) that a cited
  filing passage or event states. Otherwise the reply says the data cannot establish why.
- missing_lines (the owner's third review): each asked measure a stock has no stored figure of
  ("HDFC Bank's revenue from operations: not available in the current data."), so an answer
  never leaves a figure out silently nor puts another in its place.
"""

from app.chat.answer_check import CAUSE, Claim
from app.chat.contract import Source, Turn
from app.chat.evidence import EvidenceItem
from app.chat.lookup import SHORT_NAMES
from app.chat.understand import Question
from app.insights import METRIC_LABELS

PE_LABEL = "Price to earnings"  # app/chat/evidence.py's label for the stored P/E
SOURCE_KINDS = {
    "filing": "official filing on BSE",
    "screener": "screener.in's fundamentals table",
    "rbi": "RBI press release",
    "derived": "computed from stored figures",
}


def valuation_claims(question: Question, evidence: list[EvidenceItem]) -> list[Claim]:
    """One claim per asked stock with a price-to-earnings item, citing it."""
    claims: list[Claim] = []
    for item in evidence:
        if item.kind != "derived" or item.label != PE_LABEL or item.symbol not in question.symbols:
            continue
        name = SHORT_NAMES.get(item.symbol, item.symbol)
        reason = (item.quote or "").removeprefix("Not assessable: ")
        if item.amount == "not assessable":
            text = f"{name}'s price to earnings cannot be computed: {reason}"
        else:
            text = f"{name}'s price to earnings is {item.amount}: {reason}"
        claims.append(Claim(text=text, citations=(item.id,)))
    return claims


def previous_sources(history: list[Turn]) -> tuple[Source, ...]:
    """The sources of the most recent answer in the conversation; () if it had none."""
    for turn in reversed(history):
        if turn.role == "assistant":
            return turn.sources
    return ()


def source_items(sources: tuple[Source, ...]) -> list[EvidenceItem]:
    """The previous answer's sources as items the render step can number and link again."""
    return [
        EvidenceItem(
            id=f"S{source.marker}",
            kind="derived" if source.source == "derived" else "fact",
            symbol="",
            text=source.label,
            source=source.source,
            label=source.label,
            url=source.url,
            quote=source.quote,
        )
        for source in sources
    ]


def source_claims(items: list[EvidenceItem]) -> list[Claim]:
    return [
        Claim(text=f"{item.label} ({SOURCE_KINDS[item.source]}).", citations=(item.id,))
        for item in items
    ]


def missing_lines(question: Question, evidence: list[EvidenceItem]) -> list[str]:
    """One sentence per stock and asked measure with no figure in the evidence (for the asked
    periods, when the question names some)."""
    had = {
        (item.symbol, item.metric, item.period)
        for item in evidence
        if item.kind == "fact" and item.figures and item.rival_of is None
    }
    lines: list[str] = []
    for symbol in question.symbols:
        for metric in question.metrics:
            periods = [p for p in question.periods if (symbol, metric, p) not in had]
            if question.periods and not periods:
                continue
            if not question.periods and any((s, m) == (symbol, metric) for s, m, _ in had):
                continue
            what = METRIC_LABELS[metric].lower()
            when = f" for {', '.join(periods)}" if question.periods else ""
            name = SHORT_NAMES.get(symbol, symbol)
            lines.append(f"{name}'s {what}{when}: not available in the current data.")
    return lines


def explained(claims: list[Claim], evidence: list[EvidenceItem]) -> bool:
    """True when some claim gives a cause and cites a filing passage or event for it."""
    by_id = {item.id: item for item in evidence}
    return any(
        CAUSE.search(claim.text)
        and any(by_id[c].kind in ("passage", "event") for c in claim.citations if c in by_id)
        for claim in claims
    )
