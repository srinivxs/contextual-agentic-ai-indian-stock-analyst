"""Cutting page text into passages small enough to embed and precise enough to cite.

The one rule that matters most: a chunk never spans two pages. A citation points at ONE page, so a
passage that started on page 3 and ended on page 4 could never be cited honestly.
"""

import hashlib

from app.chunking import MAX_CHARS, chunk_pages

SENTENCE = "DemoCo revenue grew in the quarter. "  # 36 characters


def test_each_chunk_remembers_its_page_and_never_crosses_one() -> None:
    chunks = chunk_pages(["Page one text.", "Page two text.", "Page three text."])

    assert [(c.page_number, c.text) for c in chunks] == [
        (1, "Page one text."),
        (2, "Page two text."),
        (3, "Page three text."),
    ]


def test_ordinals_count_from_zero_across_the_whole_document() -> None:
    chunks = chunk_pages(["a", "b", "c"])
    assert [c.ordinal for c in chunks] == [0, 1, 2]


def test_empty_pages_produce_no_chunks_but_keep_later_page_numbers_true() -> None:
    chunks = chunk_pages(["first", "", "   ", "fourth"])
    assert [(c.ordinal, c.page_number) for c in chunks] == [(0, 1), (1, 4)]


def test_short_paragraphs_on_one_page_are_packed_together() -> None:
    chunks = chunk_pages(["Heading\n\nFirst paragraph.\n\nSecond paragraph."])
    assert len(chunks) == 1
    assert chunks[0].text == "Heading\n\nFirst paragraph.\n\nSecond paragraph."


def test_no_chunk_is_longer_than_the_limit() -> None:
    long_page = "\n\n".join([SENTENCE * 20] * 10)  # ten paragraphs of 720 characters
    chunks = chunk_pages([long_page])

    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert all(c.page_number == 1 for c in chunks)


def test_a_paragraph_longer_than_the_limit_is_split_at_sentence_boundaries() -> None:
    chunks = chunk_pages([SENTENCE * 100])  # 3,600 characters, one paragraph

    assert len(chunks) >= 3
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)  # no sentence cut in half


def test_a_sentence_longer_than_the_limit_is_split_at_word_boundaries() -> None:
    words = " ".join(["revenue"] * 400)  # 3,199 characters, no full stop anywhere
    chunks = chunk_pages([words])

    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert all(not c.text.startswith(" ") and not c.text.endswith(" ") for c in chunks)
    assert " ".join(c.text for c in chunks) == words  # nothing lost, nothing added


def test_no_text_is_lost_or_invented() -> None:
    page = "\n\n".join(f"Paragraph {n}. " + SENTENCE * 15 for n in range(8))
    chunks = chunk_pages([page])

    def words(text: str) -> list[str]:
        return text.split()

    assert [w for c in chunks for w in words(c.text)] == words(page)


def test_the_content_hash_is_the_sha256_of_the_text() -> None:
    [chunk] = chunk_pages(["Revenue was Rs 1,234 crore."])
    assert chunk.content_hash == hashlib.sha256(b"Revenue was Rs 1,234 crore.").hexdigest()


def test_the_same_pages_always_give_the_same_chunks() -> None:
    """Re-running ingestion must reproduce identical rows, which is what makes it idempotent."""
    pages = ["\n\n".join([SENTENCE * 20] * 5), "short page"]
    assert chunk_pages(pages) == chunk_pages(pages)


def test_a_single_word_longer_than_the_limit_is_cut_and_nothing_is_lost() -> None:
    """A URL or a run of digits with no spaces: the only place left to cut is inside it."""
    word = "x" * (MAX_CHARS * 2 + 7)
    chunks = chunk_pages([f"before {word} after"])

    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert "".join(c.text.replace(" ", "") for c in chunks) == f"before{word}after"


def test_a_word_of_exactly_twice_the_limit_leaves_no_empty_chunk() -> None:
    chunks = chunk_pages(["y" * (MAX_CHARS * 2)])
    assert [len(c.text) for c in chunks] == [MAX_CHARS, MAX_CHARS]
