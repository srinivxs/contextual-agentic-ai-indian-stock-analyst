"""What a chat question is about, read by code, never by the model (P12; the project notes rule 8).

A handful of word lists decide which stocks, metrics and periods a question means. Being plain
code, the reading is the same every time, costs nothing and is tested one rule at a time.

Stocks (whole words, any case), in the order the question mentions them:

    RELIANCE   reliance, ril, reliance industries
    TCS        tcs, tata consultancy, tata consultancy services
    HDFCBANK   hdfc bank, hdfcbank, hdfc

A question naming no stock ("and last year?") is a follow-up: it takes the stocks of the most
recent earlier USER turn that named some (``from_history``). The assistant's own words never
count. With no stock anywhere, the question is about all three ("which has the lowest debt?").

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
    reason      why, reason(s), explain, driver(s), drive(s), driving, drove, driven, cause(d),
                behind, due to, because: the answer may give only causes a filing states
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
"""

import re
from dataclasses import dataclass

from app.chat.contract import Turn
from app.fact_validation import parse_period
from app.vocabulary import METRICS

ALL_SYMBOLS = ("RELIANCE", "TCS", "HDFCBANK")

_STOCK_NAMES = {
    "RELIANCE": re.compile(r"\b(?:reliance industries|reliance|ril)\b", re.IGNORECASE),
    "TCS": re.compile(r"\b(?:tata consultancy services|tata consultancy|tcs)\b", re.IGNORECASE),
    "HDFCBANK": re.compile(r"\b(?:hdfc bank|hdfcbank|hdfc)\b", re.IGNORECASE),
}

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
    r"|\bp\s*/\s*e\b|\bpe\s+ratio\b|\bprice\s+to\s+earnings\b|\bvaluation\b|\bexpensive\b"
    r"|\bcheap\b|\breturns?\b(?!\s+on\s+equity)|\bvolatil\w*|\bdividend\s+yield\b",
    re.IGNORECASE,
)
# "Match me" (P14): the investor asks how the stocks fit them, not what a figure is.
_MATCH = re.compile(
    r"\bmatch(?:es)?\s+(?:me|my)\b|\b(?:suits?|fits?)\s+(?:me|my)\b|\bfit\s+for\s+me\b"
    r"|\b(?:best|right|good)\s+for\s+me\b|\bwhich\b[^.?!]*\b(?:suits?|fits?|aligns?|matches)\b",
    re.IGNORECASE,
)

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
    r"\b(?:why|reasons?|explain(?:s|ed|ing)?|drivers?|drives?|driving|drove|driven|caused?|"
    r"behind|due\s+to|because)\b",
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
    r"historical|sources?\s+of|breakdown|mix|segments?|split|composition|"
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


def symbols_in(text: str) -> tuple[str, ...]:
    """The stocks a text names, in order of first mention."""
    found = {
        symbol: match.start()
        for symbol, name in _STOCK_NAMES.items()
        if (match := name.search(text))
    }
    return tuple(sorted(found, key=found.__getitem__))


def _metrics_in(text: str) -> tuple[str, ...]:
    found: set[str] = set()
    for pattern, metrics in _METRIC_PATTERNS:
        text, count = pattern.subn(" ", text)
        if count:
            found.update(metrics)
    return tuple(name for name in METRICS if name in found)


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


def _symbols_from(question: str, history: list[Turn]) -> tuple[tuple[str, ...], bool, bool]:
    """(the stocks meant, whether they came from the history, whether any were named)."""
    named = symbols_in(question)
    if named:
        return named, False, True
    for turn in reversed(history):
        if turn.role == "user" and (earlier := symbols_in(turn.text)):
            return earlier, True, True
    return ALL_SYMBOLS, False, False


def understand(question: str, *, history: list[Turn]) -> Question:
    symbols, from_history, named = _symbols_from(question, history)
    metrics = _metrics_in(question)
    wants_growth = _GROWTH.search(question) is not None
    wants_events = _EVENTS.search(question) is not None
    wants_match = _MATCH.search(question) is not None
    wants_price = _PRICE.search(question) is not None
    wants_reason = _REASON.search(question) is not None
    years = _years_in(question)
    unread = _YEAR_IN_WORDS.sub(" ", PERIOD_TEXT.sub(" ", question))  # the periods we read, gone
    lookup = (
        bool(metrics)
        and _LOOKUP_START.search(question) is not None
        and _NOT_A_LOOKUP.search(question) is None
        and _OTHER_PERIOD.search(unread) is None
        and (years or 1) == 1
        and not (wants_growth or wants_events or wants_match or wants_price or wants_reason)
    )
    basis = _BASIS.search(question)
    return Question(
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
    )
