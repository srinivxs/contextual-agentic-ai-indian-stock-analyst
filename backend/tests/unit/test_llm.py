"""Asking a chat model (Amazon Nova on Bedrock) for a structured answer: one forced tool call (P11).

Nothing here calls AWS: a stub client stands in for Bedrock's Converse API and records what it
was asked.
"""

import copy
import logging
import threading
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.llm import BedrockLlm, LlmError, ToolAnswer, ToolSpec, bedrock_llm_client
from tests.fake_llm import FakeLlm

MODEL = "global.amazon.nova-2-lite-v1:0"

TOOL = ToolSpec(
    name="record_facts",
    description="Record the facts found on these pages.",
    schema={"type": "object", "properties": {"facts": {"type": "array"}}, "required": ["facts"]},
)

FACTS = {"facts": [{"metric": "revenue", "value_text": "1,234", "page": 3}]}


def nova_answer(
    *,
    content: Any = None,
    stop_reason: Any = "tool_use",
    usage: Any = None,
) -> dict[str, Any]:
    """The shape Converse returns when the model called a tool."""
    if content is None:
        content = [{"toolUse": {"toolUseId": "t1", "name": "record_facts", "input": FACTS}}]
    if usage is None:
        usage = {"inputTokens": 1500, "outputTokens": 120, "totalTokens": 1620}
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
        "usage": usage,
    }


def client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


class FakeConverse:
    """Answers converse like bedrock-runtime does. Each entry is an answer, or an error to raise."""

    def __init__(self, *answers: dict[str, Any] | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []
        self.threads: list[int] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(copy.deepcopy(request))
        self.threads.append(threading.get_ident())
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def ask(client: FakeConverse, *, max_tokens: int = 2000) -> ToolAnswer:
    llm = BedrockLlm(model=MODEL, client=client, max_tokens=max_tokens)
    return await llm.call(system="You record facts.", user="<document>DemoCo</document>", tool=TOOL)


async def test_it_forces_one_named_tool_at_temperature_zero() -> None:
    client = FakeConverse(nova_answer())

    await ask(client, max_tokens=1500)

    [request] = client.requests
    assert request == {
        "modelId": MODEL,
        "system": [{"text": "You record facts."}],
        "messages": [{"role": "user", "content": [{"text": "<document>DemoCo</document>"}]}],
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": "record_facts",
                        "description": "Record the facts found on these pages.",
                        "inputSchema": {"json": TOOL.schema},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "record_facts"}},
        },
        "inferenceConfig": {"temperature": 0, "maxTokens": 1500},
    }


async def test_it_returns_the_tool_input_and_the_billed_tokens() -> None:
    answer = await ask(FakeConverse(nova_answer()))

    assert answer == ToolAnswer(
        input=FACTS, input_tokens=1500, output_tokens=120, stop_reason="tool_use"
    )


async def test_it_finds_the_tool_call_after_some_text() -> None:
    """Some models say a sentence before calling the tool; the text is ignored."""
    content = [
        {"text": "Here are the facts."},
        {"toolUse": {"toolUseId": "t1", "name": "record_facts", "input": {"facts": []}}},
    ]

    answer = await ask(FakeConverse(nova_answer(content=content)))

    assert answer.input == {"facts": []}


async def test_the_default_answer_budget_is_2000_tokens() -> None:
    client = FakeConverse(nova_answer())
    llm = BedrockLlm(MODEL, client)

    await llm.call(system="s", user="u", tool=TOOL)

    assert client.requests[0]["inferenceConfig"]["maxTokens"] == 2000
    assert llm.model == MODEL


async def test_the_blocking_boto3_call_runs_in_a_worker_thread() -> None:
    client = FakeConverse(nova_answer())

    await ask(client)

    assert client.threads != [threading.get_ident()]


@pytest.mark.parametrize(
    "answer",
    [
        nova_answer(stop_reason="max_tokens"),
        nova_answer(content=[{"text": "I could not find any facts."}]),
        nova_answer(
            content=[{"toolUse": {"toolUseId": "t", "name": "other_tool", "input": FACTS}}]
        ),
        nova_answer(
            content=[{"toolUse": {"toolUseId": "t", "name": "record_facts", "input": "[]"}}]
        ),
        nova_answer(content=["not a block"]),
        nova_answer(content={"not": "a list"}),
        {"output": {"message": "not a message"}, "stopReason": "tool_use", "usage": {}},
        {"output": None, "stopReason": "tool_use", "usage": {}},
        nova_answer(usage={"inputTokens": 10}),
        nova_answer(usage={"outputTokens": 10}),
        nova_answer(usage={"inputTokens": -1, "outputTokens": 10}),
        nova_answer(usage={"inputTokens": 10, "outputTokens": -1}),
        nova_answer(usage="not usage"),
        nova_answer(stop_reason=None),
    ],
    ids=[
        "cut-off-at-max-tokens",
        "no-tool-call",
        "wrong-tool",
        "input-not-an-object",
        "block-not-an-object",
        "content-not-a-list",
        "message-not-an-object",
        "no-output",
        "no-output-tokens",
        "no-input-tokens",
        "negative-input-tokens",
        "negative-output-tokens",
        "usage-not-an-object",
        "no-stop-reason",
    ],
)
async def test_an_unusable_answer_is_refused(answer: dict[str, Any]) -> None:
    with pytest.raises(LlmError):
        await ask(FakeConverse(answer))


async def test_a_model_that_refuses_a_named_tool_choice_is_asked_once_with_any_tool() -> None:
    """Some Bedrock models accept only toolChoice "any". With one tool offered, "any" still
    forces that tool."""
    refused = client_error(
        "ValidationException", "This model doesn't support the toolChoice.tool field."
    )
    client = FakeConverse(refused, nova_answer())

    answer = await ask(client)

    assert answer.input == FACTS
    first, second = client.requests
    assert first["toolConfig"]["toolChoice"] == {"tool": {"name": "record_facts"}}
    assert second["toolConfig"]["toolChoice"] == {"any": {}}
    first["toolConfig"].pop("toolChoice")
    second["toolConfig"].pop("toolChoice")
    assert first == second


async def test_the_any_tool_retry_happens_only_once() -> None:
    refused = client_error("ValidationException", "toolChoice is not supported")
    client = FakeConverse(refused, refused, nova_answer())

    with pytest.raises(ClientError):
        await ask(client)

    assert len(client.requests) == 2


@pytest.mark.parametrize(
    "error",
    [
        client_error("ThrottlingException", "Too many requests, please wait."),
        client_error("ValidationException", "The input is too long for the model."),
        client_error("AccessDeniedException", "not authorised to use toolChoice"),
    ],
    ids=["throttled", "other-validation-error", "access-denied"],
)
async def test_any_other_aws_error_is_left_for_the_job_queue_to_retry(error: ClientError) -> None:
    client = FakeConverse(error, nova_answer())

    with pytest.raises(ClientError) as raised:
        await ask(client)

    assert raised.value is error
    assert len(client.requests) == 1


async def test_nothing_is_logged_not_the_prompt_nor_the_document(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    llm = BedrockLlm(model=MODEL, client=FakeConverse(nova_answer(), nova_answer(content=[])))

    await llm.call(system="SECRET-SYSTEM", user="SECRET-DOCUMENT", tool=TOOL)
    with pytest.raises(LlmError):
        await llm.call(system="SECRET-SYSTEM", user="SECRET-DOCUMENT", tool=TOOL)

    assert "SECRET" not in caplog.text


def test_the_client_is_for_bedrock_runtime_in_the_chosen_region() -> None:
    """Building a client needs no credentials and no network; calls would."""
    client = bedrock_llm_client("ap-south-1")
    assert client.meta.region_name == "ap-south-1"
    assert client.meta.service_model.service_name == "bedrock-runtime"


def test_the_client_waits_long_enough_for_an_answer_and_slows_down_when_throttled() -> None:
    """Writing a structured answer takes seconds, not milliseconds, so the read timeout is
    generous. P10 found this account is throttled quickly; "adaptive" retries back off."""
    config = bedrock_llm_client("ap-south-1").meta.config
    assert config.connect_timeout == 5
    assert config.read_timeout == 120
    assert config.retries["mode"] == "adaptive"
    assert config.retries["total_max_attempts"] == 10


# ---- the fake used by the rest of the test suite ----


async def test_the_fake_returns_its_answers_in_order_and_repeats_the_last() -> None:
    llm = FakeLlm([{"facts": [1]}, {"facts": [2]}], input_tokens=10, output_tokens=2)

    answers = [await llm.call(system="s", user=f"u{n}", tool=TOOL) for n in range(3)]

    assert [a.input for a in answers] == [{"facts": [1]}, {"facts": [2]}, {"facts": [2]}]
    assert answers[0] == ToolAnswer(
        input={"facts": [1]}, input_tokens=10, output_tokens=2, stop_reason="tool_use"
    )
    assert llm.calls == [("s", f"u{n}", "record_facts") for n in range(3)]
    assert llm.model == "fake-llm"


async def test_the_fake_can_answer_from_a_function_of_the_request() -> None:
    llm = FakeLlm(lambda system, user, tool: {"tool": tool.name, "user": user})

    answer = await llm.call(system="s", user="DemoCo page", tool=TOOL)

    assert answer.input == {"tool": "record_facts", "user": "DemoCo page"}
    assert (answer.input_tokens, answer.output_tokens) == (1000, 200)


async def test_the_fake_with_no_answers_finds_nothing() -> None:
    answer = await FakeLlm().call(system="s", user="u", tool=TOOL)

    assert answer.input == {}


async def test_the_fake_answers_are_copies_so_a_caller_cannot_change_them() -> None:
    llm = FakeLlm([{"facts": []}])

    first = await llm.call(system="s", user="u", tool=TOOL)
    first.input["facts"].append("changed")
    second = await llm.call(system="s", user="u", tool=TOOL)

    assert second.input == {"facts": []}


async def test_the_fake_fails_when_the_user_text_holds_the_marker() -> None:
    error = LlmError("told to fail")
    llm = FakeLlm([{"facts": []}], fail_on="=== PAGE 7 ===", error=error)

    with pytest.raises(LlmError) as raised:
        await llm.call(system="s", user="=== PAGE 7 ===\nDemoCo", tool=TOOL)
    ok = await llm.call(system="s", user="=== PAGE 8 ===\nDemoCo", tool=TOOL)

    assert raised.value is error
    assert ok.input == {"facts": []}
    assert len(llm.calls) == 2


async def test_the_fake_has_a_default_error() -> None:
    llm = FakeLlm(fail_on="DemoCo")

    with pytest.raises(RuntimeError):
        await llm.call(system="s", user="DemoCo", tool=TOOL)


@pytest.mark.parametrize(
    "answer",
    [
        nova_answer(stop_reason="max_tokens"),
        nova_answer(content=[{"text": "I will not use the tool."}]),
    ],
    ids=["cut-off", "no-tool-call"],
)
async def test_an_unusable_answer_still_reports_the_tokens_aws_billed(
    answer: dict[str, Any],
) -> None:
    """AWS bills a cut-off or tool-less answer like any other; the spending cap must count it."""
    with pytest.raises(LlmError) as refused:
        await ask(FakeConverse(answer))
    assert (refused.value.input_tokens, refused.value.output_tokens) == (1500, 120)


async def test_an_answer_without_usage_reports_no_tokens() -> None:
    with pytest.raises(LlmError) as refused:
        await ask(FakeConverse(nova_answer(stop_reason="max_tokens", usage={"inputTokens": "x"})))
    assert (refused.value.input_tokens, refused.value.output_tokens) == (0, 0)
