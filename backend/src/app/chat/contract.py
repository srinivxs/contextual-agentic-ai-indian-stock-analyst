"""The shapes every part of the chat agrees on (P12).

    api (app/api/chat.py) ──question──> ChatEngine.answer(...) ──> Reply ──> stored, returned

The api knows nothing about how an answer is made; the engine (the LangGraph workflow in
app/chat/graph.py) knows nothing about HTTP or tables. Tests of the api use a fake engine; tests of
the engine use a fake LLM. Both sides import only this module from each other.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

# answered:     a cited answer that passed the checker
# abstained:    too little evidence, or no answer passed the checker: ABSTAIN_TEXT
# out_of_scope: not about RELIANCE, TCS or HDFC Bank
# remembered:   the message only stated preferences (P13): saved, and confirmed with no LLM call
ReplyStatus = Literal["answered", "abstained", "out_of_scope", "remembered"]

ABSTAIN_TEXT = "I don't have that in the data."
OUT_OF_SCOPE_TEXT = "I can only answer about RELIANCE, TCS and HDFC Bank."


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


@dataclass(frozen=True)
class Turn:
    """An earlier message, oldest first: lets a follow-up say "and last year?"."""

    role: Literal["user", "assistant"]
    text: str


class ChatEngine(Protocol):
    async def answer(self, *, question: str, history: list[Turn], user_id: UUID) -> Reply: ...
