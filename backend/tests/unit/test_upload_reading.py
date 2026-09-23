"""Reading an uploaded PDF from the request body, without trusting the client's size or type.

The body arrives in chunks. The reader hashes as it goes and stops the moment the limit is passed,
so an oversized upload never sits in memory in full; and it checks the PDF signature itself,
because a Content-Type header is only the client's claim.
"""

import hashlib
from collections.abc import AsyncIterator, Iterable

import pytest

from app.documents import NotAPdf, TooLarge, read_pdf

PDF = b"%PDF-1.7\n" + b"0123456789" * 100


async def chunks(parts: Iterable[bytes]) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_the_bytes_and_their_sha256_come_back_together() -> None:
    upload = await read_pdf(chunks([PDF[:10], PDF[10:500], PDF[500:]]), limit=10_000)

    assert upload.data == PDF
    assert upload.sha256 == hashlib.sha256(PDF).hexdigest()
    assert upload.size == len(PDF)


async def test_a_file_exactly_at_the_limit_is_accepted() -> None:
    upload = await read_pdf(chunks([PDF]), limit=len(PDF))
    assert upload.size == len(PDF)


async def test_one_byte_over_the_limit_is_refused() -> None:
    with pytest.raises(TooLarge):
        await read_pdf(chunks([PDF, b"!"]), limit=len(PDF))


async def test_reading_stops_as_soon_as_the_limit_is_passed() -> None:
    """A 10 GB upload must not be read to the end before it is refused."""
    consumed: list[int] = []

    async def endless() -> AsyncIterator[bytes]:
        consumed.append(1)
        yield PDF
        while True:
            consumed.append(1)
            yield b"x" * 1024

    with pytest.raises(TooLarge):
        await read_pdf(endless(), limit=len(PDF) + 4096)
    assert len(consumed) <= 7


@pytest.mark.parametrize(
    "body",
    [b"", b"PK\x03\x04 a zip file", b"<html>%PDF-1.7</html>", b"%PDF", b" %PDF-1.7"],
    ids=["empty", "zip", "html", "truncated-signature", "leading-space"],
)
async def test_anything_that_does_not_start_with_the_pdf_signature_is_refused(body: bytes) -> None:
    with pytest.raises(NotAPdf):
        await read_pdf(chunks([body]), limit=10_000)


async def test_the_signature_may_arrive_split_across_chunks() -> None:
    upload = await read_pdf(chunks([b"%P", b"DF", b"-1.7\n", b"rest"]), limit=10_000)
    assert upload.data.startswith(b"%PDF-")
