"""The shapes every part of the chat agrees on (P12).

    api (app/api/chat.py) ──question──> ChatEngine.answer(...) ──> Reply ──> stored, returned

The api knows nothing about how an answer is made; the engine (the LangGraph workflow in
app/chat/graph.py) knows nothing about HTTP or tables. Tests of the api use a fake engine; tests of
the engine use a fake LLM. Both sides import only this module from each other.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

# answered:     a cited answer that passed the checker (or a stored figure stated by code)
# abstained:    one of our stocks, but the data does not answer it: ABSTAIN_TEXT, or
#               FORECAST_TEXT for a future share price
# out_of_scope: names none of our three stocks (another company, or not about companies)
# remembered:   the message only stated preferences (P13): saved, and confirmed with no LLM
#               call; or it asked what is remembered, and the profile is read back by code
ReplyStatus = Literal["answered", "abstained", "out_of_scope", "remembered"]

ABSTAIN_TEXT = "I don't have that in the data."
OUT_OF_SCOPE_TEXT = "I currently have research data only for Reliance, TCS and HDFC Bank."
FORECAST_TEXT = (
    "I don't have a verified future share-price prediction in the available data, so I can't "
    "provide one."
)
# Added by code under a "why" answer whose causes no cited filing passage or event states.
NO_EXPLANATION_TEXT = (
    "The available data confirms the change, but I don't have sufficient source material to "
    "establish the specific reasons for it."
)
# After a valuation question's stored price-to-earnings figures (app/chat/code_answers.py).
VALUATION_GAP_TEXT = (
    "Whether that makes a stock undervalued or overvalued needs a comparison basis this data does "
    "not hold: other companies' or the stock's own long-run valuation multiples, or earnings "
    "forecasts. So I can't give a verdict."
)
VALUATION_NO_DATA_TEXT = (
    "I don't have enough valuation data to judge whether it is undervalued or overvalued: no "
    "stored share price and earnings figure give a price-to-earnings ratio, and the data holds no "
    "comparison basis (other companies' or long-run valuation multiples) or earnings forecasts."
)
NO_SOURCES_TEXT = "The previous reply cited no stored source."
# Asked back when a question is unclear (the owner's third review), with choices to click.
WHICH_COMPANY_TEXT = "Which company do you mean: TCS, HDFC Bank, or Reliance?"
WHICH_MEASURE_TEXT = "What would you like me to compare: revenue, net profit, or both?"
# Added by code to a question about disagreeing sources when none of its figures disagree.
AGREE_TEXT = "The stored sources agree (within 1%) on every figure given here."


def out_of_scope_text(*companies: str | None) -> str:
    """OUT_OF_SCOPE_TEXT, naming the other companies the question itself names."""
    names = [name for name in companies if name]
    if not names:
        return OUT_OF_SCOPE_TEXT
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return f"{OUT_OF_SCOPE_TEXT} I don't have grounded data for {listed}."


@dataclass(frozen=True)
class Source:
    """One numbered source under an answer: what the [n] markers in the text point at."""

    marker: int  # 1, 2, 3 ... in order of first use
    source: Literal["filing", "screener", "rbi", "derived"]
    # "Annual report · Annual Report 2026 · p.37", "screener.in · profit-loss · ...", or for a
    # computed value "Computed: Net profit growth FY2025 → FY2026"
    label: str
    url: str | None  # the official BSE PDF at #page=N, or the screener.in page; None if computed
    quote: str | None  # the verbatim quote or passage excerpt (at most ~300 characters)


@dataclass(frozen=True)
class Choice:
    """An answer to a question asked back: the button's label, and the full question a click
    sends at once ("TCS" -> "What is the latest revenue for TCS?")."""

    label: str
    question: str


@dataclass(frozen=True)
class Reply:
    text: str  # the answer with [n] markers, or ABSTAIN_TEXT, or OUT_OF_SCOPE_TEXT
    status: ReplyStatus
    sources: tuple[Source, ...]  # empty unless status == "answered"
    model: str | None  # the LLM that wrote it; None when no LLM was called
    input_tokens: int  # what AWS billed for this answer, all calls together (0 if none)
    output_tokens: int
    # A year-by-year table shown under an answer about one stock's net profit or revenue: built
    # by code from stored screener.in figures (never by the model), so it needs no checking.
    table: "DataTable | None" = None
    # When the reply asks back: the choices, sent with this reply only (never stored).
    choices: tuple[Choice, ...] = ()


@dataclass(frozen=True)
class DataTable:
    """ "TCS net profit (consolidated, ₹ crore)": newest year first, at most five rows."""

    title: str
    columns: tuple[str, ...]  # ("Year", "Net profit (₹ crore)", "Change")
    rows: tuple[tuple[str, ...], ...]  # (("FY2026", "49,454", "+1.3%"), ...); "" = no change shown
    source_label: str  # "screener.in · profit-loss · Net Profit"
    source_url: str | None  # the screener.in company page


@dataclass(frozen=True)
class Turn:
    """An earlier message, oldest first: lets a follow-up say "and last year?", and "where did
    you get that?" be answered from the last answer's own sources."""

    role: Literal["user", "assistant"]
    text: str
    sources: tuple[Source, ...] = ()


class ChatEngine(Protocol):
    async def answer(self, *, question: str, history: list[Turn], user_id: UUID) -> Reply: ...
