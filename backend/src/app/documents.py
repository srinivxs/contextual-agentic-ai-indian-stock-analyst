"""Company documents: reading an upload safely, and recording it with its ingestion job.

Plain SQL, like the rest of the data access (ADR 013). None of these functions commits; the caller
owns the transaction.

HOW A DUPLICATE UPLOAD IS STOPPED (the P9 race)
  Two requests carrying the same file both hash it, both store it under the same key (identical
  bytes, identical place), and both run ``INSERT ... ON CONFLICT (sha256) DO NOTHING``. The unique
  constraint lets exactly one insert through; the other gets no row back and reads the existing
  document instead. The job is inserted in the same transaction as the document it belongs to, so
  a document with no job, or a job with no document, cannot exist.
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

PDF_SIGNATURE = b"%PDF-"


class TooLarge(Exception):
    """The upload passed the size limit. Reading stopped there."""


class NotAPdf(Exception):
    """The upload does not start with the PDF signature, whatever its Content-Type said."""


@dataclass(frozen=True)
class Upload:
    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


async def read_pdf(chunks: AsyncIterator[bytes], *, limit: int) -> Upload:
    """Read a request body, hashing as it goes, and stop the moment it passes ``limit`` bytes."""
    digest = hashlib.sha256()
    parts: list[bytes] = []
    size = 0
    async for chunk in chunks:
        size += len(chunk)
        if size > limit:
            raise TooLarge
        digest.update(chunk)
        parts.append(chunk)
    data = b"".join(parts)
    if not data.startswith(PDF_SIGNATURE):
        raise NotAPdf
    return Upload(data=data, sha256=digest.hexdigest())


@dataclass(frozen=True)
class DocumentView:
    """A document as any signed-in user sees it. Uploader and storage location stay inside."""

    id: int
    symbol: str
    title: str
    status: str
    size_bytes: int
    page_count: int | None
    failure_reason: str | None
    created_at: datetime
    source: str  # "upload" or "bse" (ADR 018)
    source_url: str | None  # the official public address of a fetched filing


_COLUMNS = (
    "d.id, s.symbol, d.title, d.status, d.size_bytes, d.page_count, d.failure_reason, "
    "d.created_at, d.source, d.source_url"
)

_STOCK_ID = text("SELECT id FROM stocks WHERE symbol = :symbol")

# sha256 is the arbiter. The only other unique rule, on source_url, never fires here: uploads have
# no source_url, and a fetch skips an address already recorded and is the only live job for it.
_INSERT_DOCUMENT = text(
    "INSERT INTO documents "
    "(stock_id, uploaded_by, title, sha256, size_bytes, blob_key, source, source_url) "
    "VALUES (:stock_id, :uploaded_by, :title, :sha256, :size_bytes, :blob_key, "
    ":source, :source_url) "
    "ON CONFLICT (sha256) DO NOTHING RETURNING id"
)

# ON CONFLICT names the partial unique index by its columns and predicate. The document is new, so
# this cannot conflict today; the clause keeps the insert safe if a job is ever enqueued twice.
_INSERT_JOB = text(
    "INSERT INTO jobs (kind, payload, dedupe_key) "
    "VALUES ('ingest_document', jsonb_build_object('document_id', CAST(:document_id AS bigint)), "
    ":dedupe_key) "
    "ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING"
)

_BY_SHA256 = text(
    f"SELECT {_COLUMNS} FROM documents d JOIN stocks s ON s.id = d.stock_id "  # noqa: S608 - a constant column list
    "WHERE d.sha256 = :sha256"
)

_BY_ID = text(
    f"SELECT {_COLUMNS} FROM documents d JOIN stocks s ON s.id = d.stock_id "  # noqa: S608 - a constant column list
    "WHERE d.id = :id"
)

# Newest first; the cursor is the last id the caller saw.
_PAGE = text(
    f"SELECT {_COLUMNS} FROM documents d JOIN stocks s ON s.id = d.stock_id "  # noqa: S608 - a constant column list
    "WHERE s.symbol = :symbol AND (CAST(:cursor AS bigint) IS NULL OR d.id < :cursor) "
    "ORDER BY d.id DESC LIMIT :limit"
)


def _view(row: Row[Any]) -> DocumentView:
    return DocumentView(**row._mapping)


async def stock_id(db: AsyncSession, symbol: str) -> int | None:
    found: int | None = (await db.execute(_STOCK_ID, {"symbol": symbol})).scalar_one_or_none()
    return found


async def record_upload(
    db: AsyncSession,
    *,
    stock: int,
    user_id: UUID | None,
    title: str,
    upload: Upload,
    blob_key: str,
    source: str = "upload",
    source_url: str | None = None,
) -> tuple[DocumentView, bool]:
    """Insert the document and its ingestion job, or find the existing document for this file.

    Both doors use it: an upload (a user, no source_url) and a fetched filing (no user, the BSE
    address). Returns the document and whether this call created it.
    """
    created_id = (
        await db.execute(
            _INSERT_DOCUMENT,
            {
                "stock_id": stock,
                "uploaded_by": user_id,
                "title": title,
                "sha256": upload.sha256,
                "size_bytes": upload.size,
                "blob_key": blob_key,
                "source": source,
                "source_url": source_url,
            },
        )
    ).scalar_one_or_none()
    if created_id is not None:
        await db.execute(
            _INSERT_JOB,
            {"document_id": created_id, "dedupe_key": f"ingest_document:{created_id}"},
        )
    row = (await db.execute(_BY_SHA256, {"sha256": upload.sha256})).one()
    return _view(row), created_id is not None


async def get_document(db: AsyncSession, document_id: int) -> DocumentView | None:
    row = (await db.execute(_BY_ID, {"id": document_id})).one_or_none()
    return None if row is None else _view(row)


async def list_documents(
    db: AsyncSession, symbol: str, *, cursor: int | None, limit: int
) -> tuple[list[DocumentView], int | None]:
    """One page of a stock's documents, newest first, and the cursor for the next page (or None)."""
    result = await db.execute(_PAGE, {"symbol": symbol, "cursor": cursor, "limit": limit + 1})
    views = [_view(row) for row in result]
    if len(views) > limit:
        return views[:limit], views[limit - 1].id
    return views, None
