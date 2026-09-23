"""One ingestion job: stored PDF -> page text -> chunks, recorded in the database.

Three steps, and only the first and last touch the database, each in its own short transaction.
The slow middle part (reading the file, running PDFium) holds no transaction and no row lock
(the project notes: no transaction spans I/O):

    1. [transaction] still my job? find the document, mark it processing
    2. [no transaction] read the file from the blob store, extract the pages, cut the chunks
    3. [transaction] still my job? (locks the job row) replace the pages and chunks, mark the
       document completed, complete the job

Step 3 deletes before it inserts, so running the same job twice leaves exactly one set of rows.

Two kinds of failure are told apart:

* ``DocumentRejected``: this file can never be ingested (a scan, a damaged PDF). Retrying would
  change nothing, so the job fails at once and the user gets a plain reason.
* ``JobCannotSucceed``: the job itself is malformed (no such document, an unknown kind).
* Anything else (the store did not answer, the database hiccuped) may pass, so the worker retries.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blobs import BlobStore
from app.chunking import chunk_pages
from app.jobs import ClaimedJob, complete, still_mine
from app.pdf_text import UnreadablePdf, extract_pages

# Below this many visible characters in the whole document, there is nothing to cite: a scan.
MIN_TEXT_CHARS = 50

SCANNED_REASON = (
    "No text found in this PDF: it looks like a scanned document. "
    "Upload a PDF whose text can be selected."
)
UNREADABLE_REASON = "This PDF could not be read: it may be damaged or password-protected."


class DocumentRejected(Exception):
    """The file can never be ingested. ``reason`` is shown to users as the document's status."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobCannotSucceed(Exception):
    """The job itself is wrong (no such document, an unknown kind): fail it without retrying."""


_FIND = text("SELECT blob_key FROM documents WHERE id = :id")

_MARK = text(
    "UPDATE documents SET status = :status, failure_reason = :reason, updated_at = now() "
    "WHERE id = :id"
)

_CLEAR = (
    text("DELETE FROM chunks WHERE document_id = :id"),
    text("DELETE FROM document_pages WHERE document_id = :id"),
)

_INSERT_PAGE = text(
    "INSERT INTO document_pages (document_id, page_number, text) VALUES (:id, :number, :text)"
)

_INSERT_CHUNK = text(
    "INSERT INTO chunks (document_id, ordinal, page_number, text, content_hash) "
    "VALUES (:id, :ordinal, :page_number, :text, :content_hash)"
)

_DONE = text(
    "UPDATE documents SET status = 'completed', page_count = :pages, failure_reason = NULL, "
    "updated_at = now() WHERE id = :id"
)


def document_id_of(job: ClaimedJob) -> int:
    try:
        return int(job.payload["document_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise JobCannotSucceed("the job has no valid document_id") from error


async def mark_document(
    db: AsyncSession, document_id: int, status: str, reason: str | None
) -> None:
    await db.execute(_MARK, {"id": document_id, "status": status, "reason": reason})


async def ingest_document(
    session_factory: async_sessionmaker[AsyncSession], store: BlobStore, job: ClaimedJob
) -> None:
    document_id = document_id_of(job)

    # 1. Short transaction: is it still ours, and does the document exist?
    async with session_factory() as db:
        if not await still_mine(db, job):
            return
        blob_key = (await db.execute(_FIND, {"id": document_id})).scalar_one_or_none()
        if blob_key is None:
            raise JobCannotSucceed(f"document {document_id} does not exist")
        await mark_document(db, document_id, "processing", None)
        await db.commit()

    # 2. No transaction: the slow part.
    data = await store.get(blob_key)
    try:
        pages = await asyncio.to_thread(extract_pages, data)
    except UnreadablePdf as error:
        raise DocumentRejected(UNREADABLE_REASON) from error
    if sum(len("".join(page.split())) for page in pages) < MIN_TEXT_CHARS:
        raise DocumentRejected(SCANNED_REASON)
    chunks = chunk_pages(pages)

    # 3. One transaction: replace everything this document had, then finish the job.
    async with session_factory() as db:
        if not await still_mine(db, job):
            return  # a newer claim owns the job now; its result wins
        for statement in _CLEAR:
            await db.execute(statement, {"id": document_id})
        await db.execute(
            _INSERT_PAGE,
            [
                {"id": document_id, "number": number, "text": page}
                for number, page in enumerate(pages, start=1)
            ],
        )
        # Never empty: the text check above guarantees at least one chunk.
        await db.execute(
            _INSERT_CHUNK,
            [
                {
                    "id": document_id,
                    "ordinal": chunk.ordinal,
                    "page_number": chunk.page_number,
                    "text": chunk.text,
                    "content_hash": chunk.content_hash,
                }
                for chunk in chunks
            ],
        )
        await db.execute(_DONE, {"id": document_id, "pages": len(pages)})
        await complete(db, job)
        await db.commit()
