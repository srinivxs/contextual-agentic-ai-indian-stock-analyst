"""The deterministic citation checker (P12; the project notes "RAG and citations"): the heart of the chat.

The model writes its answer as a list of claims, each citing evidence IDs (app/chat/evidence.py).
Nothing it writes is trusted. This code decides, and an empty list of problems is the only pass:

    no_claims               the answer has no claims at all
    claim_too_long          a claim longer than MAX_CLAIM_CHARS characters
    url_in_answer           a claim writes http://, https:// or www. (the model never writes an
                            address: app/chat/render.py links the real, stored sources)
    uncited_claim           a claim that cites nothing
    unknown_citation        a cited ID that is not in the evidence
    number_not_in_evidence  a number in the claim that none of THAT claim's cited items contains
    currency_mismatch       a number written as dollars (US$, USD, $) that the cited evidence does
                            not give in dollars; or written as rupees (₹, Rs, INR, crore, lakh)
                            that the cited evidence gives only in dollars
    too_many_citations      more than MAX_CITATIONS IDs on one claim (the form allows 1 to 5)
    mixed_sources           a claim that cites one measure for two periods from different
                            sources (an annual report's FY2025 revenue and screener.in's FY2026):
                            they may count it differently, so the change is not like with like;
                            the growth value (D#) compares like with like (ADR 020)
    figure_missing          a claim that cites facts, computed values or match verdicts (P14)
                            with numbers but shows none of those numbers ("TCS has low
                            leverage" citing ten figures, "TCS is a partial match"):
                            a judgment must show the figure it rests on, or there is nothing
                            for the number check to test
    cause_without_source    a claim that gives a cause ("because", "due (mainly) to", "driven
                            by", "led by", "owing to", "on account of", "as a result of",
                            "thanks to", "attributed to", "on the back of", "helped by",
                            "caused by", "boosted by", "fuelled by", "stems from",
                            "resulting from") citing
                            no filing passage (N), event (E) or match verdict (M), and no
                            computed value whose reason is its whole content (one with no
                            number, "does not apply to banks"): figures show THAT something
                            changed, never WHY
    sentiment_as_evidence   a claim citing the news sentiment that says it supports,
                            contradicts, is consistent with, confirms, reflects or
                            indicates something (the owner's second review): sentiment
                            counts good and bad news; it is not evidence about the results
    conflicting_figures     across the whole answer, two cited items rest on different stored
                            figures for one stock, measure, period and basis (an annual
                            report's FY2025 revenue in one claim, a change computed from
                            screener.in's FY2025 revenue in another): one answer, one figure.
                            Another source's figure (a rival, ``rival_of``) does not count
                            when the answer also cites the figure it differs from: citing the
                            two together discloses the difference. Figures are compared per
                            stock, measure, period and basis (one answer shows one currency
                            view of a measure, app/insights.py's measure_facts)

Numbers are compared as values, with app/fact_validation.py's ``numbers_in``: "1,23,456",
"123,456", "123456" and "123456.0" are one number, "8.8%" matches "8.8%". They are compared by
size, so "fell 3.2%" matches a change of -3.2%. Only numbers present in the cited evidence pass,
so a number the model worked out itself (a difference, a total, a rounded "about 1,200") fails,
even when the arithmetic is right: every number shown must be one a source states.

Not numbers to check (removed from the claim first): evidence IDs the model wrote into its text
("[F1]", "[D1, F2]"); periods ("FY26", "FY2026", "FY 2025-26", "2025-26", "Q3FY26", and a bare Q1
to Q4, H1, H2 or 9M); and a plain four-digit year from 1990 to 2099 when one of the cited items
mentions it (a fact for FY2026 lets the claim say "in 2026") and it is not written as money
("₹2026 crore" is an amount, not a year).

The known limit: this proves every number comes from the evidence the claim cites, not that the
evidence is true nor that the claim's words read it rightly (which of two figures is revenue, or
whether a change was a rise or a fall).
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.chat.evidence import EvidenceItem
from app.chat.understand import PERIOD_TEXT
from app.fact_validation import numbers_in, parse_number

MAX_CLAIM_CHARS = 600
MAX_CITATIONS = 5
ID_IN_DETAIL = 20  # an unknown ID is the model's text: only this much of it goes in a detail

_MARKERS = re.compile(r"\s*\[[FDMNE]\d+(?:\s*,\s*[FDMNE]\d+)*\]")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_BARE_PERIOD = re.compile(r"(?<![a-z0-9])(?:q[1-4]|[1-4]q|h[12]|9m)(?![a-z0-9])", re.IGNORECASE)
_YEAR = re.compile(r"(?<![\d,.])(?:199\d|20\d\d)(?![\d%]|[.,]\d)")
_MONEY_BEFORE = re.compile(r"(?:₹|\$|\busd|\brs\.?|\binr)\s*$", re.IGNORECASE)
CAUSE = re.compile(
    r"\b(?:because|due\s+(?:\w+\s+)?to|driven\s+by|led\s+by|owing\s+to|on\s+account\s+of|"
    r"as\s+a\s+result\s+of|thanks\s+to|attributed\s+to|on\s+the\s+back\s+of|helped\s+by|"
    r"caused\s+by|boosted\s+by|fu?ell?ed\s+by|stems?\s+from|resulting\s+from)\b",
    re.IGNORECASE,
)
_RELATION = re.compile(
    r"\b(?:support\w*|contradict\w*|consistent|inconsistent|confirm\w*|validat\w*|reflect\w*|"
    r"indicat\w*|suggest\w*|impl(?:y|ies|ied)|shows?\s+that|proves?|in\s+line\s+with|"
    r"backs?\s+up)\b",
    re.IGNORECASE,
)
SENTIMENT_LABEL = "News sentiment"  # app/chat/evidence.py's label for the rolling sentiment
_MONEY_AFTER = re.compile(
    r"\s*(?:crores?|cr|lakhs?|lacs?|millions?|mn|billions?|bn|per share)\b", re.IGNORECASE
)

# A number as printed next to a currency: "1,23,456", "1.8", "(2,353)". Its value is read by
# parse_number, the same reading numbers_in gives.
_AMOUNT = r"\(?-?\d(?:[\d,]*\d)?(?:\.\d+)?\)?"
_DOLLARS = re.compile(rf"(?:\busd|\$)\s*({_AMOUNT})", re.IGNORECASE)
_RUPEES_BEFORE = re.compile(rf"(?:₹|\brs\.?|\binr)\s*({_AMOUNT})", re.IGNORECASE)
_RUPEES_AFTER = re.compile(
    rf"(?<![\w.,])({_AMOUNT})\s*(?:crores?|cr|lakhs?|lacs?)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Claim:
    text: str
    citations: tuple[str, ...]  # evidence IDs: "F1", "D2", "N3", "E4"


@dataclass(frozen=True)
class Problem:
    code: str
    claim: int  # the claim's index, or -1 for the whole answer
    detail: str  # short; never evidence text


def without_markers(text: str) -> str:
    """The text without the evidence IDs a model may write into it ("rose [F1] 8.8%")."""
    return _MARKERS.sub("", text)


# --- numbers --------------------------------------------------------------------------------------


def _sizes(text: str) -> set[Decimal]:
    return {abs(number) for number in numbers_in(text)}


def _marked(pattern: re.Pattern[str], text: str) -> set[Decimal]:
    """The sizes of the numbers the pattern finds written next to a currency."""
    values = (parse_number(match.group(1)) for match in pattern.finditer(text))
    return {abs(value) for value in values if value is not None}


def _dollars(text: str) -> set[Decimal]:
    return _marked(_DOLLARS, text)


def _rupees(text: str) -> set[Decimal]:
    return _marked(_RUPEES_BEFORE, text) | _marked(_RUPEES_AFTER, text)


def _without_known_years(text: str, cited: list[EvidenceItem]) -> str:
    """Blank out each plain year that a cited item mentions and that is not written as money."""

    def year_or_blank(match: re.Match[str]) -> str:
        year = match.group()
        as_money = _MONEY_BEFORE.search(text[: match.start()]) or _MONEY_AFTER.match(
            text, match.end()
        )
        mentioned = any(re.search(rf"(?<!\d){year}(?!\d)", item.text) for item in cited)
        return " " if mentioned and not as_money else year

    return _YEAR.sub(year_or_blank, text)


def _checkable(text: str, cited: list[EvidenceItem]) -> str:
    """The claim with its evidence IDs, periods and known years removed."""
    text = _MARKERS.sub(" ", text)
    text = PERIOD_TEXT.sub(" ", text)
    text = _BARE_PERIOD.sub(" ", text)
    return _without_known_years(text, cited)


def _number_problems(index: int, text: str, cited: list[EvidenceItem]) -> list[Problem]:
    text = _checkable(text, cited)
    written_as_dollars, written_as_rupees = _dollars(text), _rupees(text)
    problems: list[Problem] = []
    for value in dict.fromkeys(abs(number) for number in numbers_in(text)):
        matches = [item for item in cited if value in _sizes(item.text)]
        if not matches:
            detail = f"{value} is in none of the cited evidence"
            problems.append(Problem("number_not_in_evidence", index, detail))
            continue
        in_dollars = [value in _dollars(item.text) for item in matches]
        if (value in written_as_dollars and not any(in_dollars)) or (
            value in written_as_rupees and value not in written_as_dollars and all(in_dollars)
        ):
            detail = f"{value} is not in that currency in the cited evidence"
            problems.append(Problem("currency_mismatch", index, detail))
    return problems


# --- the checker ----------------------------------------------------------------------------------


def _claim_problems(index: int, claim: Claim, evidence: dict[str, EvidenceItem]) -> list[Problem]:
    problems: list[Problem] = []
    if len(claim.text) > MAX_CLAIM_CHARS:
        problems.append(Problem("claim_too_long", index, f"over {MAX_CLAIM_CHARS} characters"))
    if _URL.search(claim.text):
        problems.append(Problem("url_in_answer", index, "the claim writes a web address"))
    if not claim.citations:
        problems.append(Problem("uncited_claim", index, "the claim cites no evidence"))
        return problems
    cited_ids = list(dict.fromkeys(claim.citations))
    for unknown in (cid for cid in cited_ids if cid not in evidence):
        detail = f"unknown id {unknown[:ID_IN_DETAIL]!r}"
        problems.append(Problem("unknown_citation", index, detail))
    cited = [evidence[cid] for cid in cited_ids if cid in evidence]
    if len(cited_ids) > MAX_CITATIONS:
        detail = f"{len(cited_ids)} citations; at most {MAX_CITATIONS}"
        problems.append(Problem("too_many_citations", index, detail))
    problems += _number_problems(index, claim.text, cited)
    for metric in _mixed_metrics(cited):
        detail = f"{metric} over time from different sources; cite the growth value"
        problems.append(Problem("mixed_sources", index, detail))
    if CAUSE.search(claim.text) and not any(_states_reasons(item) for item in cited):
        detail = "a reason needs a cited filing passage (N) or event (E) that states it"
        problems.append(Problem("cause_without_source", index, detail))
    if _RELATION.search(claim.text) and any(i.label == SENTIMENT_LABEL for i in cited):
        detail = "news sentiment is not evidence about the results; report it on its own"
        problems.append(Problem("sentiment_as_evidence", index, detail))
    if not problems and _figure_missing(claim.text, cited):  # only once nothing else is wrong
        detail = "show at least one figure from the cited facts or values"
        problems.append(Problem("figure_missing", index, detail))
    return problems


def _mixed_metrics(cited: list[EvidenceItem]) -> list[str]:
    """The measures the claim cites for more than one period from more than one source."""
    periods: dict[str, set[str]] = {}
    sources: dict[str, set[str]] = {}
    for item in cited:
        if item.kind == "fact" and item.metric and item.period:
            periods.setdefault(item.metric, set()).add(item.period)
            sources.setdefault(item.metric, set()).add(item.source)
    return [m for m in periods if len(periods[m]) > 1 and len(sources[m]) > 1]


def _states_reasons(item: EvidenceItem) -> bool:
    """A passage, an event or a match verdict can state a reason; so can a computed value that
    is only its reason ("does not apply to banks"). A figure never can."""
    if item.kind in ("passage", "event", "match"):
        return True
    return item.kind == "derived" and not _sizes(_checkable(item.text, [item]))


def _conflicts(claims: list[Claim], evidence: dict[str, EvidenceItem]) -> list[Problem]:
    """Each claim whose cited items rest on a different stored figure for a stock, measure,
    period and basis than an earlier citation in the answer did. A rival cited with the figure
    it differs from is a disclosure and is left out, whatever the order of citation."""
    cited = {cid for claim in claims for cid in claim.citations}
    first: dict[tuple[str, str, str, str], tuple[Decimal, str]] = {}
    problems: list[Problem] = []
    for index, claim in enumerate(claims):
        for cid in dict.fromkeys(claim.citations):
            item = evidence.get(cid)
            if item is None or item.rival_of in cited:
                continue
            for figure in item.figures:
                key = (figure.symbol, figure.metric, figure.period, figure.basis)
                value, first_id = first.setdefault(key, (figure.value, cid))
                detail = (
                    f"{cid} and {first_id} rest on different figures for {figure.metric} "
                    f"{figure.period}; cite one of them"
                )
                problem = Problem("conflicting_figures", index, detail)
                if value != figure.value and problem not in problems:
                    problems.append(problem)
    return problems


def _figure_missing(text: str, cited: list[EvidenceItem]) -> bool:
    """True when the cited facts and computed values have numbers and the claim shows none."""
    figures: set[Decimal] = set()
    for item in cited:
        if item.kind in ("fact", "derived", "match"):
            figures |= _sizes(_checkable(item.text, [item]))
    return bool(figures) and not (figures & _sizes(_checkable(text, cited)))


def check_answer(claims: list[Claim], evidence: list[EvidenceItem]) -> list[Problem]:
    """Every problem with the answer, claim by claim; an empty list means it passes."""
    if not claims:
        return [Problem("no_claims", -1, "the answer has no claims")]
    by_id = {item.id: item for item in evidence}
    return [
        *(
            problem
            for index, claim in enumerate(claims)
            for problem in _claim_problems(index, claim, by_id)
        ),
        *_conflicts(claims, by_id),
    ]
