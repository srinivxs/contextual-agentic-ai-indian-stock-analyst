"""Company documents: recording a fetched filing with its ingestion job, and reading them back.

Every document is an official filing the worker fetched from BSE (ADR 018). Users do not upload
documents: the brief asks the app to ingest data by itself, and the owner removed the upload
door once automatic filings worked (P9d).

Plain SQL, like the rest of the data access (ADR 013). None of these functions commits; the caller
owns the transaction.

HOW A DUPLICATE IS STOPPED
  Two fetches of the same bytes (the same filing listed twice, a re-run, two workers racing) both
  store the file under the same key (identical bytes, identical place) and both run
  ``INSERT ... ON CONFLICT (sha256) DO NOTHING``. The unique constraint lets exactly one insert
  through; the other gets no row back and reads the existing document instead. The ingestion job is
  inserted in the same transaction as its document, so neither can exist without the other.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

PDF_SIGNATURE = b"%PDF-"


@dataclass(frozen=True)
class PdfFile:
    """A downloaded PDF and its SHA-256, which names it everywhere (blob key, dedupe)."""

    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class DocumentView:
    """A document as any signed-in user sees it. Where the copy is stored stays inside."""

    id: int
    symbol: str
    title: str
    status: str
    size_bytes: int
    page_count: int | None
    failure_reason: str | None
    created_at: datetime
    source: str  # "bse" (ADR 018)
    source_url: str | None  # the official public address the filing was fetched from
    kind: str | None  # transcript, presentation, annual_report or announcement
    period: str | None  # "Jul 2026", "Annual Report 2025", an announcement's subject


_COLUMNS = (
    "d.id, s.symbol, d.title, d.status, d.size_bytes, d.page_count, d.failure_reason, "
    "d.created_at, d.source, d.source_url, d.kind, d.period"
)

_STOCK_ID = text("SELECT id FROM stocks WHERE symbol = :symbol")

# sha256 is the arbiter. The only other unique rule, on source_url, never fires here: a fetch skips
# an address already recorded, and it is the only live job for that address.
_INSERT_DOCUMENT = text(
    "INSERT INTO documents "
    "(stock_id, title, sha256, size_bytes, blob_key, source, source_url, kind, period) "
    "VALUES (:stock_id, :title, :sha256, :size_bytes, :blob_key, 'bse', :source_url, "
    ":kind, :period) "
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


async def record_document(
    db: AsyncSession,
    *,
    stock: int,
    title: str,
    pdf: PdfFile,
    blob_key: str,
    source_url: str,
    kind: str,
    period: str,
) -> tuple[DocumentView, bool]:
    """Insert a fetched filing and its ingestion job, or find the existing document for these bytes.

    Returns the document and whether this call created it.
    """
    created_id = (
        await db.execute(
            _INSERT_DOCUMENT,
            {
                "stock_id": stock,
                "title": title,
                "sha256": pdf.sha256,
                "size_bytes": pdf.size,
                "blob_key": blob_key,
                "source_url": source_url,
                "kind": kind,
                "period": period,
            },
        )
    ).scalar_one_or_none()
    if created_id is not None:
        await db.execute(
            _INSERT_JOB,
            {"document_id": created_id, "dedupe_key": f"ingest_document:{created_id}"},
        )
    row = (await db.execute(_BY_SHA256, {"sha256": pdf.sha256})).one()
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
