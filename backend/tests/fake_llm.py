"""A chat model that never calls AWS (the project notes: fake LLM in tests).

It returns the answers it was given, so a test decides exactly what "the model" says, including
wrong or hostile answers, and checks that the deterministic code around it copes.
"""

import copy
from collections.abc import Callable
from typing import Any

from app.llm import ToolAnswer, ToolSpec

AnswerFunction = Callable[[str, str, ToolSpec], dict[str, Any]]


class FakeLlm:
    """``answers``: a list returned in order (the last one repeats), or a function of
    (system, user, tool). With neither, every answer is ``{}`` ("found nothing")."""

    model = "fake-llm"

    def __init__(
        self,
        answers: list[dict[str, Any]] | AnswerFunction | None = None,
        *,
        input_tokens: int = 1000,
        output_tokens: int = 200,
        fail_on: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, str]] = []  # (system, user, tool name)
        self._answers = answers if answers is not None else [{}]
        self._answered = 0
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._fail_on = fail_on
        self._error = error or RuntimeError("the fake LLM was told to fail")

    async def call(self, *, system: str, user: str, tool: ToolSpec) -> ToolAnswer:
        self.calls.append((system, user, tool.name))
        if self._fail_on is not None and self._fail_on in user:
            raise self._error
        if isinstance(self._answers, list):
            answer = self._answers[min(self._answered, len(self._answers) - 1)]
        else:
            answer = self._answers(system, user, tool)
        self._answered += 1
        return ToolAnswer(
            input=copy.deepcopy(answer),  # a caller changing it must not change the next answer
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            stop_reason="tool_use",
        )
