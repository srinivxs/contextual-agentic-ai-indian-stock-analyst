"""A question that only asks for stored figures, answered by code (the owner's review, 2026-09-29).

"What was Reliance's revenue in FY2024?" needs no model: the figure is a stored row with its
source (the project notes rule 8). app/chat/understand.py decides the question is a lookup; this module
writes one sentence per asked figure from the evidence items (app/chat/evidence.py), each citing
its item, so the answer, its marker and its source are the stored ones by construction:

    "Reliance's revenue from operations for FY2024 was ₹9,14,472 crore (consolidated;
    annual report)."

- the periods the question names, else the newest figure of each asked measure;
- each measure as itself: a bank's net interest income is never given as its "revenue";
- only the figure an item rests on is given, never another source's differing one (app/chat/
  render.py's disclosures then names that one, with its own source);
- a measure a stock has no figure of at all is left out (code then says it is not available,
  app/chat/code_answers.py's missing_lines); if a named period is missing although the measure
  has other periods, or no stock was named, it returns None and the model answers instead (a
  passage may hold the figure, or the model says it is not in the data).
"""

from app.chat.answer_check import Claim
from app.chat.evidence import EvidenceItem, source_word
from app.chat.understand import Question
from app.insights import METRIC_LABELS

SHORT_NAMES = {"RELIANCE": "Reliance", "TCS": "TCS", "HDFCBANK": "HDFC Bank"}


def _sentence(item: EvidenceItem) -> str:
    figure = item.figures[0]
    what = METRIC_LABELS[figure.metric].lower()
    basis = "basis not stated" if figure.basis == "unspecified" else figure.basis
    source = source_word(item.label)
    source = source if source.startswith("screener") else source.lower()
    called = f', reported as "{item.reported_as}"' if item.reported_as else ""
    return (
        f"{SHORT_NAMES.get(item.symbol, item.symbol)}'s {what} for {figure.period} was "
        f"{item.amount} ({basis}; {source}{called})."
    )


def lookup_claims(question: Question, evidence: list[EvidenceItem]) -> list[Claim] | None:
    """One claim per asked figure a stock has (possibly none), or None when code cannot
    answer the question."""
    if not (question.lookup and question.named):
        return None
    # the facts the answer rests on; another source's differing figure is never the one given
    facts = [
        item
        for item in evidence
        if item.kind == "fact" and item.figures and item.amount and item.rival_of is None
    ]
    claims: list[Claim] = []
    for symbol in question.symbols:
        for metric in question.metrics:
            items = [i for i in facts if i.symbol == symbol and i.metric == metric]
            if not items:
                continue  # none at all: not available, said by code
            if question.periods:
                found = {item.period: item for item in reversed(items)}  # the first per period
                if any(period not in found for period in question.periods):
                    return None
                chosen = [found[period] for period in question.periods]
            else:
                chosen = items[:1]  # newest first, as the evidence lists them
            claims += [Claim(text=_sentence(item), citations=(item.id,)) for item in chosen]
    return claims
