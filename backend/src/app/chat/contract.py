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
# remembered:   the message only stated preferences (P13): saved, and confirmed with no LLM call
ReplyStatus = Literal["answered", "abstained", "out_of_scope", "remembered"]

ABSTAIN_TEXT = "I don't have that in the data."
OUT_OF_SCOPE_TEXT = "I currently have research data only for Reliance, TCS and HDFC Bank."
FORECAST_TEXT = (
    "I don't have a verified future share-price prediction in the available data, so I can't "
    "provide one."
)
# Added by code under a "why" answer that cites no filing passage or event.
NO_EXPLANATION_TEXT = (
    "The available data shows the change, but I don't have sufficient source material to "
    "establish why it occurred."
)


def out_of_scope_text(company: str | None) -> str:
    """OUT_OF_SCOPE_TEXT, naming the other company when the question itself names it."""
    if not company:
        return OUT_OF_SCOPE_TEXT
    return f"{OUT_OF_SCOPE_TEXT} I don't have grounded data for {company}."


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
    """An earlier message, oldest first: lets a follow-up say "and last year?"."""

    role: Literal["user", "assistant"]
    text: str


class ChatEngine(Protocol):
    async def answer(self, *, question: str, history: list[Turn], user_id: UUID) -> Reply: ...
