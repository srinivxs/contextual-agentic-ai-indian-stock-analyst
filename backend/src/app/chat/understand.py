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
compare(d), yoy. Events: news, event(s), sentiment, announcement(s), announced.

Periods: every "FY26", "FY2026", "FY'26", "FY 2025-26", "2025-26", "Q3 FY26", "Q3FY2026" or
"3QFY26" in the text, read by app/fact_validation.py's ``parse_period`` into codes such as
"FY2026" and "Q3FY2026". A half year or nine months ("H1 FY26", "9MFY26") is none of our periods
and is dropped rather than read as the full year.
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
    (r"profits?|pat|earnings|net income", ("net_profit",)),
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

# A fiscal year, with an optional quarter or part-of-a-year in front ("Q3 FY26", "3QFY26",
# "H1 FY26"), or a year range ("2025-26"). Also used by app/chat/answer_check.py to set periods
# aside before it compares numbers.
PERIOD_TEXT = re.compile(
    r"(?<![a-z0-9])(?:(?:h[12]|9m|q[1-4]|[1-4]q)\s*(?:-|of)?\s*)?fy\s*'?\d{2,4}(?:\s*-\s*\d{2,4})?(?!\d)"
    r"|(?<![\d,.])20\d{2}\s*-\s*(?:20)?\d{2}(?!\d)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Question:
    symbols: tuple[str, ...]  # in order of mention
    metrics: tuple[str, ...]  # vocabulary metric names, in vocabulary order; () = none named
    wants_growth: bool
    wants_events: bool
    periods: tuple[str, ...]  # "FY2026", "Q3FY2026", ... in order of mention
    from_history: bool  # the stocks came from an earlier user turn


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
    codes: list[str] = []
    for match in PERIOD_TEXT.finditer(text):
        period = parse_period(match.group(), document_month=None)
        if period is not None and period.code not in codes:
            codes.append(period.code)
    return tuple(codes)


def _symbols_from(question: str, history: list[Turn]) -> tuple[tuple[str, ...], bool]:
    """(the stocks meant, whether they came from the history)."""
    named = symbols_in(question)
    if named:
        return named, False
    for turn in reversed(history):
        if turn.role == "user" and (earlier := symbols_in(turn.text)):
            return earlier, True
    return ALL_SYMBOLS, False


def understand(question: str, *, history: list[Turn]) -> Question:
    symbols, from_history = _symbols_from(question, history)
    return Question(
        symbols=symbols,
        metrics=_metrics_in(question),
        wants_growth=_GROWTH.search(question) is not None,
        wants_events=_EVENTS.search(question) is not None,
        periods=_periods_in(question),
        from_history=from_history,
    )
