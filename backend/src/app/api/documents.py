"""Reading a stock's documents: the official filings the worker fetched from BSE (ADR 018).

Read-only. Documents enter the system only through the worker; there is no upload endpoint (the
brief asks the app to ingest data by itself; removed in P9d). Both routes need a session.

The stored file is never served back through this API; users see the document's metadata and a
link to the official public address it came from.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel

from app.api.stocks import Symbol
from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.documents import DocumentView, get_document, list_documents, stock_id

router = APIRouter(prefix="/api/v1", tags=["documents"])

DocumentId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class DocumentOut(BaseModel):
    id: int
    symbol: str
    title: str
    status: str
    size_bytes: int
    page_count: int | None
    failure_reason: str | None
    created_at: str
    # "bse" (ADR 018). source_url is the official address the UI links to.
    source: str
    source_url: str | None
    # What the filing is (transcript, presentation, annual_report, announcement) and the period it
    # covers. The Documents page groups and orders by them.
    kind: str | None
    period: str | None


class DocumentsResponse(BaseModel):
    items: list[DocumentOut]
    next_cursor: int | None


def _out(view: DocumentView) -> DocumentOut:
    return DocumentOut(**{**vars(view), "created_at": view.created_at.isoformat()})


@router.get("/stocks/{symbol}/documents", summary="A stock's documents, newest first")
async def stock_documents(
    symbol: Symbol,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    cursor: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentsResponse:
    async with request.app.state.session_factory() as db:
        if await stock_id(db, symbol) is None:
            # Deliberately does not repeat the symbol the caller sent.
            raise AppError(status_code=404, code="not_found", message="No such stock")
        views, next_cursor = await list_documents(db, symbol, cursor=cursor, limit=limit)
    return DocumentsResponse(items=[_out(view) for view in views], next_cursor=next_cursor)


@router.get("/documents/{document_id}", summary="One document: poll this to follow its status")
async def document(
    document_id: DocumentId,
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> DocumentOut:
    async with request.app.state.session_factory() as db:
        view = await get_document(db, document_id)
    if view is None:
        raise AppError(status_code=404, code="not_found", message="No such document")
    return _out(view)
