"""Page text -> chunks: passages small enough to embed, precise enough to cite.

Rules, in order of importance:

1. A chunk never spans two pages. Citations point at one page (P12), so each chunk belongs to one.
2. No text is lost or invented: the chunks of a page, read in order, are the page's words.
3. Split at the most natural boundary that fits: paragraph, then sentence, then word.
4. Deterministic: the same pages always give the same chunks, so re-running ingestion reproduces
   identical rows.

MAX_CHARS is about 300 tokens: short enough that a retrieved chunk is about one thing, long enough
to keep a table row or a sentence with its context. There is no overlap between chunks; neighbours
are easy to fetch by ordinal if retrieval ever needs more context.
"""

import hashlib
import re
from dataclasses import dataclass

MAX_CHARS = 1200

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    ordinal: int  # position in the whole document, from 0
    page_number: int  # from 1
    text: str
    content_hash: str  # SHA-256 of the text; P10 reuses an embedding for identical text


def _split_words(text: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= MAX_CHARS:
            current = candidate
        else:
            if current:
                pieces.append(current)
            # A "word" longer than the limit (a URL, a run of digits) is cut where it must be.
            while len(word) > MAX_CHARS:
                pieces.append(word[:MAX_CHARS])
                word = word[MAX_CHARS:]
            current = word
    # Never empty here: this is only called with a non-empty sentence, and the loop above always
    # leaves at least one character of the last word in `current`.
    pieces.append(current)
    return pieces


def _split_paragraph(paragraph: str) -> list[str]:
    """A long paragraph as pieces of at most MAX_CHARS, cut between sentences where possible."""
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        pieces.extend([sentence] if len(sentence) <= MAX_CHARS else _split_words(sentence))
    return pieces


def _pack(pieces: list[str], separator: str) -> list[str]:
    """Join neighbouring pieces while the result still fits."""
    packed: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{separator}{piece}" if current else piece
        if len(candidate) <= MAX_CHARS:
            current = candidate
        else:
            packed.append(current)
            current = piece
    if current:
        packed.append(current)
    return packed


def _chunk_page(text: str) -> list[str]:
    chunks: list[str] = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for paragraph in paragraphs:
        if len(paragraph) <= MAX_CHARS:
            chunks.append(paragraph)
        else:
            chunks.extend(_pack(_split_paragraph(paragraph), " "))
    return _pack(chunks, "\n\n")


def chunk_pages(pages: list[str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page_number, text in enumerate(pages, start=1):
        for piece in _chunk_page(text):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    page_number=page_number,
                    text=piece,
                    content_hash=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                )
            )
    return chunks
