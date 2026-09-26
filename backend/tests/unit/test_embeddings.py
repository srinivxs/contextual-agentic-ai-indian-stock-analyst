"""Turning text into a meaning fingerprint (an embedding) with Amazon Titan on Bedrock (P10).

Nothing here calls AWS: a fake client stands in for Bedrock and records what it was asked.
"""

import io
import json
import math
from typing import Any

import pytest

from app.embeddings import (
    DIMENSIONS,
    BedrockEmbedder,
    EmbeddingError,
    bedrock_client,
    vector_literal,
)
from tests.fakes import FakeEmbedder

MODEL = "amazon.titan-embed-text-v2:0"


class FakeBedrock:
    """Answers invoke_model the way bedrock-runtime does: a JSON body in a readable stream."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    def invoke_model(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {"body": io.BytesIO(json.dumps(self.answer).encode())}


def titan_answer(*, length: int = DIMENSIONS, tokens: int = 7) -> dict[str, Any]:
    return {"embedding": [0.01] * length, "inputTextTokenCount": tokens}


async def test_it_asks_titan_for_a_normalised_1024_number_fingerprint() -> None:
    client = FakeBedrock(titan_answer())
    embedder = BedrockEmbedder(model=MODEL, client=client)

    await embedder.embed("Revenue rose 12% in the quarter.")

    [request] = client.requests
    assert request["modelId"] == MODEL
    assert request["contentType"] == "application/json"
    assert request["accept"] == "application/json"
    assert json.loads(request["body"]) == {
        "inputText": "Revenue rose 12% in the quarter.",
        "dimensions": 1024,
        "normalize": True,
    }


async def test_it_returns_the_vector_and_how_many_tokens_were_billed() -> None:
    embedder = BedrockEmbedder(model=MODEL, client=FakeBedrock(titan_answer(tokens=9)))

    embedding = await embedder.embed("text")

    assert len(embedding.vector) == DIMENSIONS
    assert embedding.tokens == 9
    assert embedder.model == MODEL


@pytest.mark.parametrize(
    "answer",
    [
        titan_answer(length=512),  # the wrong size would not fit the vector(1024) column
        {"embedding": ["x"] * DIMENSIONS, "inputTextTokenCount": 1},
        {"embedding": [float("nan")] * DIMENSIONS, "inputTextTokenCount": 1},
        {"inputTextTokenCount": 1},
        {"embedding": [0.1] * DIMENSIONS},
        {"embedding": [0.1] * DIMENSIONS, "inputTextTokenCount": -1},
    ],
    ids=["wrong-length", "not-numbers", "nan", "no-vector", "no-token-count", "negative-tokens"],
)
async def test_an_answer_that_is_not_a_valid_fingerprint_is_refused(answer: dict[str, Any]) -> None:
    embedder = BedrockEmbedder(model=MODEL, client=FakeBedrock(answer))
    with pytest.raises(EmbeddingError):
        await embedder.embed("text")


def test_the_client_is_for_bedrock_runtime_in_the_chosen_region() -> None:
    """Building a client needs no credentials and no network; calls would."""
    client = bedrock_client("ap-south-1")
    assert client.meta.region_name == "ap-south-1"
    assert client.meta.service_model.service_name == "bedrock-runtime"


def test_a_vector_is_written_the_way_pgvector_reads_it() -> None:
    assert vector_literal([0.5, -1.0, 2.25e-05]) == "[0.5,-1.0,2.25e-05]"


async def test_the_fake_embedder_is_deterministic_and_normalised() -> None:
    first = FakeEmbedder()
    second = FakeEmbedder()

    a = await first.embed("DemoCo revenue grew")
    b = await second.embed("DemoCo revenue grew")

    assert a == b
    assert math.isclose(math.sqrt(sum(x * x for x in a.vector)), 1.0)
    assert first.calls == ["DemoCo revenue grew"]
