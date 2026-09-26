"""Meaning fingerprints (embeddings): text in, 1,024 numbers out (P10, ADR 019).

Texts about the same thing get vectors that point the same way, even when they share no words
("attrition" and "employees leaving"), which is what lets search work by meaning.

The rest of the code sees only ``Embedder``: ``embed(text)`` and the ``model`` name, which is
stored with every vector so vectors of two models are never compared. Real runs use Amazon Titan
Text Embeddings V2 on Bedrock (ADR 010); tests use a fake (tests/fakes.py), so no test calls AWS.

boto3 is synchronous, so each call runs in a worker thread (``asyncio.to_thread``) and never blocks
the event loop. Credentials are never passed in: boto3 finds them itself, from the environment
locally and from the ECS task role in AWS.
"""

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from botocore.config import Config

# ADR 010: Titan V2 at 1,024 dimensions; the embeddings column is vector(1024).
DIMENSIONS = 1024


@dataclass(frozen=True)
class Embedding:
    vector: list[float]
    tokens: int  # what AWS billed for this text


class Embedder(Protocol):
    model: str

    async def embed(self, text: str) -> Embedding: ...


class EmbeddingError(Exception):
    """The model's answer was not a usable fingerprint."""


def bedrock_client(region: str) -> Any:
    """A bedrock-runtime client. Creating it needs no credentials and makes no request."""
    config = Config(
        connect_timeout=5,
        read_timeout=30,
        # boto3's own retries absorb brief throttling; the job queue retries anything longer.
        retries={"max_attempts": 3, "mode": "standard"},
    )
    return boto3.client("bedrock-runtime", region_name=region, config=config)


class BedrockEmbedder:
    def __init__(self, *, model: str, client: Any) -> None:
        self.model = model
        self._client = client

    async def embed(self, text: str) -> Embedding:
        body = json.dumps({"inputText": text, "dimensions": DIMENSIONS, "normalize": True})
        response = await asyncio.to_thread(
            self._client.invoke_model,
            modelId=self.model,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        return _parse(json.loads(response["body"].read()))


def _parse(answer: Any) -> Embedding:
    vector = answer.get("embedding") if isinstance(answer, dict) else None
    tokens = answer.get("inputTextTokenCount") if isinstance(answer, dict) else None
    if not isinstance(vector, list) or len(vector) != DIMENSIONS:
        raise EmbeddingError(f"expected {DIMENSIONS} numbers")
    if not all(isinstance(x, int | float) and math.isfinite(x) for x in vector):
        raise EmbeddingError("the fingerprint holds something other than finite numbers")
    if not isinstance(tokens, int) or tokens < 0:
        raise EmbeddingError("no token count")
    return Embedding(vector=[float(x) for x in vector], tokens=tokens)


def vector_literal(vector: list[float]) -> str:
    """The text form pgvector reads, ``[0.1,0.2,...]``: passed as a parameter, cast in SQL."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"
