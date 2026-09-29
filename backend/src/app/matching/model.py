"""The shapes of a match (P14; the project notes "Matching"). Every part of P14 agrees on this module.

    profile (app/memory) + a stock's stored facts and events (app/insights_store.StockRows)
        ──> app/matching/rules.py: match_stock()  (code only, no LLM)
        ──> StockMatch: a status and the reasons, each with its figure and citations

A reason is one criterion the profile asks for, judged on one stored figure:

    outcome         meaning                                             effect on the status
    pass            the figure meets the criterion                      none
    miss            a soft criterion is not met                         match -> partial
    fail            a HARD criterion is not met                         -> no_match
    not_assessable  cannot be judged here (debt for a bank; P/E after a   ignored, but shown
                    bonus or split, or with no prices at all;
                    a loss)
    no_data         the figure is not in the data                       hard: not_enough_data

Status of a stock: any fail -> no_match; else a hard no_data -> not_enough_data; else nothing
judged (no pass, miss or fail) -> not_enough_data; else every reason that is not
not_assessable passed -> match (so a soft no_data leaves it partial); else partial.
Cautions (negative news sentiment) never change the status; they are shown beside it.
"""

from dataclasses import dataclass
from typing import Literal

from app.insights import Citation

Status = Literal["match", "partial", "no_match", "not_enough_data"]
Outcome = Literal["pass", "miss", "fail", "not_assessable", "no_data"]

# The criteria, in the order reasons are listed.
Criterion = Literal[
    "debt",  # debt to equity: avoid_high_debt (hard, <= 1.0); conservative (soft, <= 0.5)
    "dividend",  # income style: the latest dividend per share is above zero
    "revenue_growth",  # growth style or aggressive: latest revenue (a bank: net interest income)
    "profit_growth",  # growth style: >= 10%; stability and conservative: >= 0% (no fall)
    "quality",  # quality style: latest return on equity >= 15%
    "value",  # value style: P/E <= 20 from the latest close and full-year basic EPS (ADR 025)
    "momentum",  # momentum style: six-month price return >= 0; else EARNINGS momentum
    "horizon",  # short term: one-year volatility <= 30%; long term: profit did not fall
    "sentiment",  # cautions only: the rolling news sentiment is negative
]


@dataclass(frozen=True)
class Reason:
    criterion: Criterion
    preference: str  # the profile value that asks for it: "avoid_high_debt", "income", ...
    hard: bool
    outcome: Outcome
    # One plain sentence with the figure and the threshold, e.g. "Debt to equity is 0.11, within
    # the 1.0 limit for avoiding high debt." Money as reported, never converted.
    text: str
    citations: tuple[Citation, ...]  # the stored rows the figure comes from; () if none


@dataclass(frozen=True)
class StockMatch:
    symbol: str
    name: str
    status: Status
    reasons: tuple[Reason, ...]  # in Criterion order, one per criterion
    cautions: tuple[Reason, ...]  # e.g. negative rolling news sentiment, citing its events
