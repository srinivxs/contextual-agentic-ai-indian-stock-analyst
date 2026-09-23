"""Uploading a company document, and reading what has been uploaded.

The upload is the raw PDF as the request body (``Content-Type: application/pdf``), with the title
in the query string. No multipart form: one file per request needs no extra parser, and the body
can be read chunk by chunk and refused the moment it passes the size limit.

Checks run in the same order as the follow endpoints (ADR 013): **Origin, then session, then
validation**. Then, in order, and each one before anything is stored:

    stock exists? -> Content-Type is PDF? -> declared size under the limit? -> read the body,
    stopping at the limit -> it really starts with %PDF- -> store the file -> one short
    transaction: the document and its ingestion job

The file is stored BEFORE the transaction, so no transaction is ever held open across I/O
(the project notes). A crash between the two leaves a stored file with no document, which is harmless:
the next upload of the same bytes writes to the same key and records it.

The original file is never served back through this API; users see the document's metadata only.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel
from starlette.responses import Response

from app.api.stocks import Symbol
from app.auth.deps import current_user, require_same_origin
from app.auth.sessions import CurrentUser
from app.blobs import BlobStore, blob_key_for
from app.core.errors import AppError
from app.documents import (
    DocumentView,
    NotAPdf,
    TooLarge,
    get_document,
    list_documents,
    read_pdf,
    record_upload,
    stock_id,
)

router = APIRouter(prefix="/api/v1", tags=["documents"])

# At least one non-space character (the pattern is a search, not a full match). Stored stripped.
Title = Annotated[str, Query(min_length=1, max_length=200, pattern=r"\S")]
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
    # "upload", or "bse" for a filing fetched automatically (ADR 018). For those, source_url is
    # the official address the UI links to; the stored file itself is never served.
    source: str
    source_url: str | None
    # What a fetched filing is (transcript, presentation, annual_report, announcement) and the
    # period it covers; both None for an upload. The Documents page groups and orders by them.
    kind: str | None
    period: str | None


class DocumentsResponse(BaseModel):
    items: list[DocumentOut]
    next_cursor: int | None


def _out(view: DocumentView) -> DocumentOut:
    return DocumentOut(**{**vars(view), "created_at": view.created_at.isoformat()})


def _no_such_stock() -> AppError:
    # Deliberately does not repeat the symbol the caller sent.
    return AppError(status_code=404, code="not_found", message="No such stock")


def _not_a_pdf() -> AppError:
    return AppError(
        status_code=415, code="unsupported_media_type", message="Only PDF files can be uploaded"
    )


def _too_large(limit: int) -> AppError:
    return AppError(
        status_code=413,
        code="payload_too_large",
        message=f"The file is larger than the {limit // (1024 * 1024)} MB limit",
    )


@router.post(
    "/stocks/{symbol}/documents",
    summary="Upload a company document (PDF) for a stock",
    responses={200: {"description": "This file was already uploaded; the existing document"}},
    status_code=201,
    dependencies=[Depends(require_same_origin)],
)
async def upload_document(
    symbol: Symbol,
    title: Title,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> DocumentOut:
    settings = request.app.state.settings
    limit: int = settings.upload_max_bytes

    async with request.app.state.session_factory() as db:
        stock = await stock_id(db, symbol)
    if stock is None:
        raise _no_such_stock()

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/pdf":
        raise _not_a_pdf()
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)  # refused before a single byte is read

    try:
        upload = await read_pdf(request.stream(), limit=limit)
    except TooLarge:
        raise _too_large(limit) from None
    except NotAPdf:
        raise _not_a_pdf() from None

    key = blob_key_for(upload.sha256)
    store: BlobStore = request.app.state.blob_store
    await store.put(key, upload.data)

    async with request.app.state.session_factory() as db:
        document, created = await record_upload(
            db, stock=stock, user_id=user.id, title=title.strip(), upload=upload, blob_key=key
        )
        await db.commit()

    if document.symbol != symbol:
        raise AppError(
            status_code=409,
            code="conflict",
            message=f"This file was already uploaded for {document.symbol}",
        )
    response.status_code = 201 if created else 200
    return _out(document)


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
            raise _no_such_stock()
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
