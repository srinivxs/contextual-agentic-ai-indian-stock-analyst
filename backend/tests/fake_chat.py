"""A chat engine that never calls AWS or reads the database (project rule: fakes in tests).

The api tests use it to decide exactly what "the engine" answers, and to see what the api handed
it: the question, the earlier turns (oldest first) and whose question it was.
"""

from collections.abc import Callable
from uuid import UUID

from app.chat.contract import ABSTAIN_TEXT, DataTable, Reply, Source, Turn

ReplyFunction = Callable[[str, list[Turn]], Reply]

DEMO_SOURCE = Source(
    marker=1,
    source="filing",
    label="Annual report · DemoCo Annual Report 2026 · p.44",
    url="https://www.bseindia.com/xml-data/corpfiling/AttachHis/00000001-demo.pdf#page=44",
    quote="DemoCo's net profit for FY2026 was ₹110 crore.",
)


def answered(
    text: str = "DemoCo's net profit was ₹110 crore in FY2026 [1].",
    *,
    sources: tuple[Source, ...] = (DEMO_SOURCE,),
    input_tokens: int = 1000,
    output_tokens: int = 200,
    table: DataTable | None = None,
) -> Reply:
    return Reply(
        text=text,
        status="answered",
        sources=sources,
        model="fake-llm",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        table=table,
    )


def abstained() -> Reply:
    return Reply(
        text=ABSTAIN_TEXT,
        status="abstained",
        sources=(),
        model=None,
        input_tokens=0,
        output_tokens=0,
    )


def remembered(text: str = "Got it, I'll remember you're conservative.") -> Reply:
    """A message that only stated preferences (P13): saved, and confirmed with no LLM call."""
    return Reply(
        text=text, status="remembered", sources=(), model=None, input_tokens=0, output_tokens=0
    )


class FakeChatEngine:
    """``replies``: a list returned in order (the last one repeats), or a function of
    (question, history). With neither, every reply is ``answered()``. ``error``: raised instead of
    replying, after the call is recorded."""

    def __init__(
        self,
        replies: list[Reply] | ReplyFunction | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, list[Turn], UUID]] = []  # (question, history, user_id)
        self._replies = replies if replies is not None else [answered()]
        self._error = error

    async def answer(self, *, question: str, history: list[Turn], user_id: UUID) -> Reply:
        self.calls.append((question, list(history), user_id))
        if self._error is not None:
            raise self._error
        if isinstance(self._replies, list):
            return self._replies[min(len(self.calls) - 1, len(self._replies) - 1)]
        return self._replies(question, history)
