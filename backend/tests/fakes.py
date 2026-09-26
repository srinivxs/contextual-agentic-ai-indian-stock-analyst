"""Fakes for tests: an embedder that never calls AWS.

CI and every test run use this instead of Bedrock (the project notes: fake LLM/embedder in tests), so no
test can spend money or needs credentials.
"""

import hashlib
import math
import re

from app.embeddings import DIMENSIONS, Embedding

_WORD = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """A bag of words, hashed into 1,024 buckets and normalised.

    Not clever, but it behaves like a real embedder where the tests need it to: the same text
    always gives the same vector, and texts sharing words point in similar directions, so "which
    chunk is closest to this question?" has a sensible answer.
    """

    model = "fake-bag-of-words"

    def __init__(self, *, fail_on: str | None = None, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on
        self._error = error or RuntimeError("the fake embedder was told to fail")

    async def embed(self, text: str) -> Embedding:
        self.calls.append(text)
        if self._fail_on is not None and self._fail_on in text:
            raise self._error
        words = _WORD.findall(text.lower())
        vector = [0.0] * DIMENSIONS
        for word in words:
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
        if not words:
            vector[0] = 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return Embedding(vector=[x / norm for x in vector], tokens=max(len(words), 1))
