"""What a chat question is about, read by code, never by the model (P12; the project notes rule 8).

A handful of word lists decide which stocks, metrics and periods a question means. Being plain
code, the reading is the same every time, costs nothing and is tested one rule at a time.

Stocks (whole words, any case), in the order the question mentions them:

    RELIANCE   reliance, ril, reliance industries
    TCS        tcs, tata consultancy, tata consultancy services
    HDFCBANK   hdfc bank, hdfcbank, hdfc

A question naming no stock is a follow-up only if it refers back: it opens with "and",
"also", "then", "what about" or "how about", or says "its", "it", "their", "they", "them",
"same" or "the company/stock/bank/firm/group". A follow-up takes the stocks of the most recent
earlier USER turn that named some (``from_history``); the assistant's own words never count.
Anything else naming no stock is about all three ("which has the lowest debt?"), so "What is
zomato revenue?" after a question about TCS can never be answered with TCS's figures (the
owner's second review).

Metrics (whole words, any case). The longer phrases are read first and then blanked out, so
"return on equity" is not also "equity" and "earnings per share" is not also "earnings":

    earnings per share, eps                        eps_basic
    return on equity, roe                          return_on_equity
    net interest margin, nim, margin(s)            net_interest_margin
    net interest income, nii                       net_interest_income
    gross npa(s), npa(s), bad loan(s),
      asset quality                                gross_npa_ratio
    dividend(s), payout(s), dps                    dividend_per_share
    debt, borrowing(s), leverage                   total_borrowings
    equity, net worth                              total_equity
    revenue(s), sales, top line, turnover          revenue_from_operations and net_interest_income
                                                   (a bank's top line is its net interest income)
    profit(s), pat, earnings, net income           net_profit

Growth: grow, growth, change, increase, decrease, rise, fall (and their other forms), vs, versus,
compare(d), yoy. Events: news, event(s), sentiment, announcement(s), announced. A match (P14):
"match me/my", "suits/fits me/my", "best/right/good for me", "which ... suits/fits/aligns/matches".
Prices (ADR 025): share/stock price, close(d), trading at, P/E, price to earnings, valuation,
expensive, cheap, return(s) (not "return on equity"), volatile/volatility, dividend yield.

Periods: every "FY26", "FY2026", "FY'26", "FY 2025-26", "2025-26", "Q3 FY26", "Q3FY2026" or
"3QFY26" in the text, read by app/fact_validation.py's ``parse_period`` into codes such as
"FY2026" and "Q3FY2026". A half year or nine months ("H1 FY26", "9MFY26") is none of our periods
and is dropped rather than read as the full year. A year end also names its fiscal year: "(year
ended) March 2024", "Mar 2024", "fiscal 2024", "financial year 2024" are FY2024 (a "March 2025
quarter" is not a year). "earnings call(s)", "earnings presentation" and the like name a document,
not the profit metric.

What kind of question it is (the owner's review, 2026-09-29), again by word lists:

    named       a stock is named in the question or an earlier user turn (else all three are
                searched, and a question about another company can be refused as out of scope)
    reason      why, reason(s), driver(s), drive(s), driving, drove, driven, cause(d), behind,
                due to, because, "what explains", "explain why / the change (rise, fall ...)"
                (not "explain whether"): the answer may give only causes a filing states
    forecast    a share price question that looks ahead: will, going to, next year/quarter/...,
                forecast, predict, projection, target price, price target, future. Refused by code:
                end-of-day closes cannot predict a price
    lookup      "what is/was/were/are ...", "what's ..." or "how much ..." asking for a metric of
                ours, with no growth, reason, news, price, match or judgment word (good, high,
                better, stable, trend, since, between, over the ...), no word asking for more
                than a figure ("sources of", breakdown, mix, segments, debt to equity), no window of
                years, and no period we cannot read ("last quarter", "Q3", a bare "2024"):
                answered by code from the stored figure, without the model (app/chat/lookup.py)
    years       "last/past/previous N years" (at most 5); "latest", "most recent", "current
                year", "last year" mean 1; otherwise None (the answer takes the latest three)
    basis       "standalone" or "consolidated" when the question says so
    others      companies that are not ours (app/chat/entities.py): such a question never
                takes a stock from the conversation, and it is refused before any retrieval
    qualified   a metric word with a qualifier we do not store ("AI revenue", "retail revenue",
                "operating profit"): never a lookup of the total

The intent (the owner's second review): one per question, the first that applies, in this order.
It decides the route (app/chat/graph.py); the flags above still decide what evidence is loaded.

    unsupported_company   another company is named                       refused, no retrieval
    memory_read           "what do you remember about my preferences",   the profile, by code
                          "what are my preferences"
    future_unsupported    a future share price (forecast)                refused by code
    source_request        "where did you get that", "what is the         the last answer's
                          source" (no stock or metric named)             sources, by code
    personalized          "which suits/fits my preferences", "which      matching (P14)
                          should I research/consider/buy"
    valuation             undervalued, overvalued, fair value, worth     P/E and what is
                          buying, valuation, "is/looks cheap",           missing, by code
                          "is/looks expensive" (before explanation:
                          "why is TCS undervalued?" gets the P/E and
                          what a verdict would need)
    explanation           why, what drove ... (reason)                   model, causes only
                                                                         from documents
    source_conflict       inconsistencies, discrepancies, disagree,      model, and every
                          different figures, differ between sources      disagreement by code
    fact_lookup           lookup (above)                                 stated by code
    calculation           calculate, compute, work out                   model + computed values
    comparison            compare, vs, versus, higher, lower, better     model
    trend                 growth words, or a window of years             model
    news                  news, events, sentiment                        model
    price                 share prices                                   model
    general               anything else                                  model

A message may also state preferences (memory is written from any message, app/memory/extract.py);
one that only states them ends at "remembered" before any of this.
"""

import re
from dataclasses import dataclass, replace
from typing import Literal

from app.chat.contract import Turn
from app.chat.entities import ends_with_our_name, other_companies, symbols_in
from app.fact_validation import parse_period
from app.vocabulary import METRICS

ALL_SYMBOLS = ("RELIANCE", "TCS", "HDFCBANK")

# Read in this order; each match is blanked out before the next pattern looks.
_METRIC_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"earnings per share|eps", ("eps_basic",)),
    (r"return on equity|roe", ("return_on_equity",)),
    (r"net interest margin|nim|margins?", ("net_interest_margin",)),
    (r"net interest income|nii", ("net_interest_income",)),
    (r"gross npas?|npas?|bad loans?|asset quality", ("gross_npa_ratio",)),
    (r"dividends?|payouts?|dps", ("dividend_per_share",)),
    (r"debt|borrowings?|leverage", ("total_borrowings",)),
    (r"equity|net worth", ("total_equity",)),
    (r"revenues?|sales|top line|turnover", ("revenue_from_operations", "net_interest_income")),
    (
        r"profits?|pat|earnings(?!\s+(?:calls?|conferences?|presentations?|releases?|updates?|"
        r"transcripts?|reports?))|net income",
        ("net_profit",),
    ),
)
_METRIC_PATTERNS = [
    (re.compile(rf"\b(?:{words})\b", re.IGNORECASE), metrics) for words, metrics in _METRIC_WORDS
]

_GROWTH = re.compile(
    r"\b(?:grow|grows|grew|grown|growing|growth|change|changes|changed|increase|increases|"
    r"increased|decrease|decreases|decreased|rise|rises|rose|risen|fall|falls|fell|fallen|"
    r"vs|versus|compare|compared|comparison|yoy)\b",
    re.IGNORECASE,
)
_EVENTS = re.compile(r"\b(?:news|events?|sentiment|announcements?|announced)\b", re.IGNORECASE)
# Share prices (ADR 025): the price, where it closed, returns, valuation, volatility, yield.
# "return on equity" is a fundamentals ratio, not a price return.
_PRICE = re.compile(
    r"\b(?:share|stock)\s+prices?\b|\bprices?\b|\bclos(?:e|ed|ing)\b|\btrad(?:ing|ed)\s+at\b"
    r"|\bp\s*/\s*e\b|\bpe\s+ratio\b|\bprice\s+to\s+earnings\b|\bvaluation\b"
    r"|\breturns?\b(?!\s+on\s+equity)|\bvolatil\w*|\bdividend\s+yield\b",
    re.IGNORECASE,
)
# "Match me" (P14): the investor asks how the stocks fit them, not what a figure is.
_MATCH = re.compile(
    r"\bmatch(?:es)?\s+(?:me|my)\b|\b(?:suits?|fits?)\s+(?:me|my)\b|\bfit\s+for\s+me\b"
    r"|\b(?:best|right|good)\s+for\s+me\b|\bwhich\b[^.?!]*\b(?:suits?|fits?|aligns?|matches)\b"
    r"|\bmy\s+(?:stated\s+|investment\s+|investor\s+|own\s+)*(?:preferences|profile|criteria)\b"
    r"|\bshould\s+i\s+(?:research|consider|look\s+(?:at|into)|focus\s+on|buy|invest\s+in|pick|"
    r"choose|prefer|shortlist)\b",
    re.IGNORECASE,
)
# What the investor asks us to recall (P13 memory, read back by code).
_MEMORY_ASK = re.compile(
    r"\b(?:do|did|have|can)\s+you\s+(?:remember|recall|know|keep|store|stored|save|saved)\b",
    re.IGNORECASE,
)
_ABOUT_ME = re.compile(r"\b(?:me|my|preferences|profile)\b", re.IGNORECASE)
_MY_PROFILE = re.compile(
    r"\b(?:what\s+(?:are|is)|what's|show(?:\s+me)?|list|tell\s+me)\s+my\s+(?:\w+\s+){0,2}"
    r"(?:preferences|profile)\b|\bwhat\s+my\s+(?:\w+\s+){0,2}(?:preferences|profile)\s+(?:are|is)\b"
    r"|^\s*what\s+(?:do|did)\s+you\s+(?:remember|recall)\b",
    re.IGNORECASE,
)
# A question that refers back to the stock of an earlier turn.
_FOLLOW_UP = re.compile(
    r"^\s*(?:and|also|plus|then|so|what\s+about|how\s+about)\b|\b(?:its|it|it's|their|they|them|"
    r"same)\b|\bthe\s+(?:company|stock|bank|firm|group|business)(?:'s)?\b",
    re.IGNORECASE,
)
# Where the last answer came from.
_SOURCES_ASK = re.compile(
    r"\bwhere\s+(?:exactly\s+)?(?:did|do|does)\s+(?:you|that|this|it|these|those)\s+"
    r"(?:get|find|come|obtain|take)\b|\b(?:what|which)\s+(?:is|are|was|were)?\s*(?:the|your)?\s*"
    r"(?:exact\s+)?sources?\b|\bcite\s+(?:your|the)\s+sources?\b",
    re.IGNORECASE,
)
_VALUATION = re.compile(
    r"\b(?:under|over)[- ]?valued\b|\bfair(?:ly)?[- ]valued\b|\bfair\s+value\b|"
    r"\bintrinsic\s+value\b|\bworth\s+(?:buying|investing\s+in|it)\b|\bvaluations?\b|"
    r"\b(?:is|are|looks?|seems?)\s+(?:\w+\s+){0,3}?(?:cheap|expensive)\b",
    re.IGNORECASE,
)
_CONFLICTS = re.compile(
    r"\b(?:inconsisten\w*|discrepanc\w*|disagree\w*|conflict\w*)\b"
    r"|\bdiffer\w*\s+(?:between|across|among)\s+(?:the\s+|your\s+)?sources\b"
    r"|\bdifferent\s+(?:\w+\s+){0,4}(?:figures|values|numbers)\b",
    re.IGNORECASE,
)
_CALCULATION = re.compile(r"\b(?:calculate|calculation|compute|work\s+out)\b", re.IGNORECASE)
_COMPARISON = re.compile(
    r"\b(?:compare\w*|comparison|vs\.?|versus|higher|lower|bigger|smaller|better|worse)\b",
    re.IGNORECASE,
)
# Words that may stand right before a metric word without changing what it measures. Any other
# word ("AI revenue", "retail revenue", "operating profit") asks for a part we do not store.
_PLAIN_BEFORE_METRIC = {
    *("the", "its", "their", "total", "net", "gross", "consolidated", "standalone", "reported"),
    *("annual", "yearly", "overall", "latest", "full", "full-year", "year", "company", "group"),
    *("basic", "diluted", "per", "what", "what's", "is", "was", "are", "were", "much", "many"),
    *("of", "in", "and", "or", "for", "on", "a", "an", "my", "your", "same", "bank", "this"),
    *("that", "these", "those", "company's"),
}

# A fiscal year, with an optional quarter or part-of-a-year in front ("Q3 FY26", "3QFY26",
# "H1 FY26"), or a year range ("2025-26"). Also used by app/chat/answer_check.py to set periods
# aside before it compares numbers.
PERIOD_TEXT = re.compile(
    r"(?<![a-z0-9])(?:(?:h[12]|9m|q[1-4]|[1-4]q)\s*(?:-|of)?\s*)?fy\s*'?\d{2,4}(?:\s*-\s*\d{2,4})?(?!\d)"
    r"|(?<![\d,.])20\d{2}\s*-\s*(?:20)?\d{2}(?!\d)",
    re.IGNORECASE,
)
# A year end or a fiscal year in words: "(year ended) March 2024", "31 Mar 2024", "fiscal 2024",
# "financial year 2024". Not a "March 2025 quarter", a "quarter ended March 2024" or a
# "Q4 March 2024".
_YEAR_IN_WORDS = re.compile(
    r"\b(?:(?:31(?:st)?\s+)?mar(?:ch)?\.?\s+(?:31,?\s+)?|(?:fiscal|financial)\s+(?:year\s+)?)"
    r"(20\d{2})\b(?!\s+quarter)",
    re.IGNORECASE,
)
_QUARTER_BEFORE = re.compile(r"\b(?:quarters?|qtr|q[1-4]|[1-4]q)\b[\w\s,]{0,15}$", re.I)
_REASON = re.compile(
    r"\b(?:why|reasons?|drivers?|drives?|driving|drove|driven|caused?|behind|due\s+to|because|"
    r"what\s+explains?|explain\w*\s+(?:why|how\s+come|the\s+(?:change|rise|fall|increase|"
    r"decrease|drop|growth|decline|jump)))\b",
    re.IGNORECASE,
)
_FUTURE = re.compile(
    r"\b(?:will|going\s+to|next\s+(?:year|month|week|quarter|fiscal)|forecast\w*|predict\w*|"
    r"projections?|target\s+price|price\s+target|future)\b",
    re.IGNORECASE,
)
_LOOKUP_START = re.compile(r"^\s*(?:what(?:'s|\s+(?:is|was|were|are)\b)|how\s+much\b)", re.I)
# Words that ask for more than one stored figure: a judgment, a comparison over time, what a
# figure is made of, or a ratio computed from two figures.
_NOT_A_LOOKUP = re.compile(
    r"\b(?:good|bad|high|higher|highest|low|lower|lowest|strong|stronger|weak|weaker|stable|"
    r"healthy|better|worse|best|worst|enough|trends?|since|between|over\s+the|history|"
    r"historical|sources?\s+of(?![^?.]*\b(?:figure|number|value|data)\b)|breakdown|mix|"
    r"segments?|split|composition|"
    r"debt\s*(?:to|-to-|/)\s*equity)\b",
    re.IGNORECASE,
)
# A period word left over once the periods we read are taken out: "last quarter", "Q3", "2024".
_OTHER_PERIOD = re.compile(
    r"\b(?:20\d{2}|q[1-4]|[1-4]q|h[12]|9m|quarters?|quarterly|half[- ]year|months?)\b",
    re.IGNORECASE,
)
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5}
_LAST_YEARS = re.compile(
    r"\b(?:last|past|previous)\s+(\d{1,2}|two|three|four|five)\s+(?:fiscal\s+|financial\s+)?years?\b",
    re.IGNORECASE,
)
_LATEST = re.compile(
    r"\b(?:latest|most\s+recent|(?:current|last)\s+(?:fiscal\s+|financial\s+)?year)\b",
    re.IGNORECASE,
)
MAX_YEARS = 5
_BASIS = re.compile(r"\b(standalone|consolidated)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Question:
    symbols: tuple[str, ...]  # in order of mention
    metrics: tuple[str, ...]  # vocabulary metric names, in vocabulary order; () = none named
    wants_growth: bool
    wants_events: bool
    periods: tuple[str, ...]  # "FY2026", "Q3FY2026", ... in order of mention
    from_history: bool  # the stocks came from an earlier user turn
    wants_match: bool = False  # "Match me", "which one suits me?" (P14)
    wants_price: bool = False  # the share price, returns, valuation (ADR 025)
    named: bool = False  # the question or an earlier user turn names the stocks
    wants_reason: bool = False  # why, what explains, what drove ...
    wants_forecast: bool = False  # a future share price
    lookup: bool = False  # asks for a stored figure, nothing more
    years: int | None = None  # "last 3 years" -> 3, "latest" -> 1; None: not said
    basis: str | None = None  # "standalone" / "consolidated" when the question says so
    others: tuple[str, ...] = ()  # companies that are not ours, as the question writes them
    wants_memory: bool = False  # "what do you remember about my preferences?"
    wants_sources: bool = False  # "where did you get that?"
    wants_valuation: bool = False  # "is TCS undervalued?"
    wants_conflicts: bool = False  # "any inconsistencies between your sources?"
    intent: "Intent" = "general"


Intent = Literal[
    "unsupported_company",
    "memory_read",
    "future_unsupported",
    "source_request",
    "personalized",
    "valuation",
    "explanation",
    "source_conflict",
    "fact_lookup",
    "calculation",
    "comparison",
    "trend",
    "news",
    "price",
    "general",
]


def _metrics_in(text: str) -> tuple[str, ...]:
    found: set[str] = set()
    for pattern, metrics in _METRIC_PATTERNS:
        text, count = pattern.subn(" ", text)
        if count:
            found.update(metrics)
    return tuple(name for name in METRICS if name in found)


def _qualified(text: str) -> bool:
    """True when a metric word has a word before it that narrows what it measures."""
    for pattern, _ in _METRIC_PATTERNS:
        for match in pattern.finditer(text):
            before = text[: match.start()].replace("\u2019", "'")
            words = before.split()
            word = words[-1].lower().strip(",;:") if words else ""
            plain = (  # a possessive counts only when it is ours ("Reliance's revenue")
                not word
                or word in _PLAIN_BEFORE_METRIC
                or ends_with_our_name(before)
                or _OTHER_PERIOD.fullmatch(word) is not None
                or PERIOD_TEXT.fullmatch(word) is not None
            )
            if not plain:
                return True
    return False


def _periods_in(text: str) -> tuple[str, ...]:
    """The period codes in order of mention, each once."""
    found: list[tuple[int, str]] = []
    for match in PERIOD_TEXT.finditer(text):
        period = parse_period(match.group(), document_month=None)
        if period is not None:
            found.append((match.start(), period.code))
    for match in _YEAR_IN_WORDS.finditer(text):
        is_range = re.match(r"\s*-\s*\d", text[match.end() :])  # "financial year 2023-24"
        is_quarter = _QUARTER_BEFORE.search(text[: match.start()])  # "quarter ended March 2024"
        if not (is_range or is_quarter):
            found.append((match.start(), f"FY{match.group(1)}"))
    return tuple(dict.fromkeys(code for _, code in sorted(found)))


def _years_in(text: str) -> int | None:
    if match := _LAST_YEARS.search(text):
        word = match.group(1).lower()
        return min(_NUMBER_WORDS.get(word) or int(word), MAX_YEARS)
    return 1 if _LATEST.search(text) else None


def _symbols_from(
    question: str, history: list[Turn], others: tuple[str, ...]
) -> tuple[tuple[str, ...], bool, bool]:
    """(the stocks meant, whether they came from the history, whether any were named). A
    question naming another company never takes our stocks from the conversation."""
    named = symbols_in(question)
    if named:
        return named, False, True
    refers_back = not others and _FOLLOW_UP.search(question) is not None
    for turn in reversed(history if refers_back else []):
        if turn.role == "user" and (earlier := symbols_in(turn.text)):
            return earlier, True, True
    return ALL_SYMBOLS, False, False


def _intent(question: Question, text: str) -> Intent:
    """The first intent that applies, in the order of the table in the module docstring."""
    if question.others:
        return "unsupported_company"
    if question.wants_memory:
        return "memory_read"
    if question.wants_forecast:
        return "future_unsupported"
    if question.wants_sources:
        return "source_request"
    if question.wants_match:
        return "personalized"
    if question.wants_valuation:
        return "valuation"
    if question.wants_reason:
        return "explanation"
    if question.wants_conflicts and not question.lookup:
        return "source_conflict"
    if question.lookup:
        return "fact_lookup"
    if _CALCULATION.search(text):
        return "calculation"
    if _COMPARISON.search(text):
        return "comparison"
    if question.wants_growth or (question.years or 0) > 1:
        return "trend"
    if question.wants_events:
        return "news"
    return "price" if question.wants_price else "general"


def understand(question: str, *, history: list[Turn]) -> Question:
    others = other_companies(question)
    symbols, from_history, named = _symbols_from(question, history, others)
    metrics = _metrics_in(question)
    wants_growth = _GROWTH.search(question) is not None
    wants_events = _EVENTS.search(question) is not None
    wants_match = _MATCH.search(question) is not None
    wants_valuation = _VALUATION.search(question) is not None
    wants_price = wants_valuation or _PRICE.search(question) is not None
    wants_reason = _REASON.search(question) is not None
    wants_memory = bool(
        (_MEMORY_ASK.search(question) and _ABOUT_ME.search(question))
        or _MY_PROFILE.search(question)
    )
    years = _years_in(question)
    unread = _YEAR_IN_WORDS.sub(" ", PERIOD_TEXT.sub(" ", question))  # the periods we read, gone
    lookup = (
        named
        and bool(metrics)
        and _LOOKUP_START.search(question) is not None
        and _NOT_A_LOOKUP.search(question) is None
        and _OTHER_PERIOD.search(unread) is None
        and (years or 1) == 1
        and not _qualified(question)
        and not (wants_growth or wants_events or wants_match or wants_price or wants_reason)
        and not (others or wants_memory)
    )
    basis = _BASIS.search(question)
    read = Question(
        symbols=symbols,
        metrics=metrics,
        wants_growth=wants_growth,
        wants_events=wants_events,
        periods=_periods_in(question),
        from_history=from_history,
        wants_match=wants_match,
        wants_price=wants_price,
        named=named,
        wants_reason=wants_reason,
        wants_forecast=wants_price and _FUTURE.search(question) is not None,
        lookup=lookup,
        years=years,
        basis=basis.group(1).lower() if basis else None,
        others=others,
        wants_memory=wants_memory,
        wants_sources=(
            _SOURCES_ASK.search(question) is not None and not metrics and not symbols_in(question)
        ),
        wants_valuation=wants_valuation,
        wants_conflicts=_CONFLICTS.search(question) is not None,
    )
    return replace(read, intent=_intent(read, question))
