"""Asking a chat model for a structured answer: one forced tool call (P11, ADR 010).

The rest of the code sees only ``StructuredLlm``: ``call(system, user, tool)`` returns the
arguments the model filled in for that one tool, plus the tokens AWS billed. Real runs use Amazon
Nova 2 Lite on Bedrock through the Converse API; tests use a fake (tests/fake_llm.py), so no test
calls AWS.

Why a tool call: we describe the answer we want as a JSON Schema ("a list of facts, each with
these fields"), offer it as the only tool and *force* the model to call it. What comes back is
then already a parsed object in a known shape, not free text we would have to pick JSON out of.
The tool is never "run": it has no side effects, it is only a form for the model to fill in.

boto3 is synchronous, so each call runs in a worker thread (``asyncio.to_thread``) and never
blocks the event loop. Credentials are never passed in: boto3 finds them itself. Nothing here
logs: prompts carry filing text, and the project notes forbids logging prompts or document text.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]  # a JSON Schema object describing the tool's input


@dataclass(frozen=True)
class ToolAnswer:
    input: dict[str, Any]  # what the model filled in for the tool
    input_tokens: int  # what AWS billed for the prompt
    output_tokens: int  # what AWS billed for the answer
    stop_reason: str


class LlmError(Exception):
    """The model's answer was unusable: no tool call, the wrong tool, cut off, or malformed.

    It carries the tokens AWS billed for the answer anyway (0 when the answer did not say), so the
    spending cap can count a wasted call like any other.
    """

    def __init__(self, message: str, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class StructuredLlm(Protocol):
    model: str

    async def call(self, *, system: str, user: str, tool: ToolSpec) -> ToolAnswer: ...


def bedrock_llm_client(region: str) -> Any:
    """A bedrock-runtime client. Creating it needs no credentials and makes no request."""
    config = Config(
        connect_timeout=5,
        # Writing a structured answer over many pages takes seconds, not milliseconds.
        read_timeout=120,
        # P10 found this account is throttled quickly: "adaptive" slows its own sending rate
        # as well as retrying. The job queue retries anything longer.
        retries={"total_max_attempts": 10, "mode": "adaptive"},
    )
    return boto3.client("bedrock-runtime", region_name=region, config=config)


class BedrockLlm:
    def __init__(self, model: str, client: Any, max_tokens: int = 2000) -> None:
        self.model = model
        self._client = client
        self._max_tokens = max_tokens

    async def call(self, *, system: str, user: str, tool: ToolSpec) -> ToolAnswer:
        named = {"tool": {"name": tool.name}}
        try:
            response = await self._converse(system, user, tool, tool_choice=named)
        except ClientError as error:
            if not _refuses_named_tool_choice(error):
                raise  # throttling, expired credentials...: the job queue retries the job
            # Some models accept only "any". With a single tool offered, "any" still forces it.
            response = await self._converse(system, user, tool, tool_choice={"any": {}})
        return _parse(response, tool.name)

    async def _converse(
        self, system: str, user: str, tool: ToolSpec, *, tool_choice: dict[str, Any]
    ) -> Any:
        return await asyncio.to_thread(
            self._client.converse,
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": {"json": tool.schema},
                        }
                    }
                ],
                "toolChoice": tool_choice,
            },
            # Temperature 0: the same pages should give the same facts every time.
            inferenceConfig={"temperature": 0, "maxTokens": self._max_tokens},
        )


def _refuses_named_tool_choice(error: ClientError) -> bool:
    details = error.response.get("Error", {})
    code = details.get("Code")
    message = str(details.get("Message", ""))
    return code == "ValidationException" and "toolchoice" in message.lower()


def _billed(response: Any) -> tuple[int | None, int | None]:
    """The input and output tokens the answer reports; None where missing or malformed."""
    usage = response.get("usage")
    counts = []
    for key in ("inputTokens", "outputTokens"):
        value = usage.get(key) if isinstance(usage, dict) else None
        counts.append(value if isinstance(value, int) and value >= 0 else None)
    return counts[0], counts[1]


def _parse(response: Any, tool_name: str) -> ToolAnswer:
    input_tokens, output_tokens = _billed(response)
    try:
        stop_reason = response.get("stopReason")
        if not isinstance(stop_reason, str):
            raise LlmError("no stop reason")
        if stop_reason == "max_tokens":
            # A cut-off answer may be a valid but incomplete list: refuse it rather than keep half.
            raise LlmError("the answer was cut off at the token limit")
        tool_input = _tool_input(response, tool_name)
    except LlmError as error:
        raise LlmError(
            str(error), input_tokens=input_tokens or 0, output_tokens=output_tokens or 0
        ) from None
    if input_tokens is None:
        raise LlmError("no input token count")
    if output_tokens is None:
        raise LlmError("no output token count")
    return ToolAnswer(
        input=tool_input,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
    )


def _tool_input(response: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """The input of the first call to ``tool_name``; any text the model wrote is ignored."""
    output = response.get("output")
    message = output.get("message") if isinstance(output, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        raise LlmError("the answer has no content")
    for block in content:
        tool_use = block.get("toolUse") if isinstance(block, dict) else None
        if isinstance(tool_use, dict) and tool_use.get("name") == tool_name:
            tool_input = tool_use.get("input")
            if not isinstance(tool_input, dict):
                raise LlmError("the tool input is not an object")
            return tool_input
    raise LlmError(f"the model did not call {tool_name}")
