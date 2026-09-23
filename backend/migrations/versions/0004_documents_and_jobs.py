"""documents, document_pages, chunks and jobs: the ingestion pipeline's tables (P9)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23

One migration for the whole phase, as the project notes asks. Every rule that makes ingestion idempotent is
a constraint here, so concurrent writers converge on one row without any application-level lock:

* ``documents.sha256`` is UNIQUE: one file, one document, however many times it is uploaded.
  It is the ONLY unique constraint on documents. ``blob_key`` is derived from the hash (a CHECK
  says so) and needs none of its own: a second unique constraint would be a second ON CONFLICT
  arbiter, and ``ON CONFLICT (sha256) DO NOTHING`` does not cover it. The first draft had one, and
  eight concurrent uploads of one file made two of them fail with a unique violation on blob_key.
* ``jobs`` has a partial unique index on ``dedupe_key`` WHERE the job is pending or processing:
  at most one live job per piece of work, while a finished job never blocks a re-run (ADR 005).
* ``chunks`` is unique on (document, ordinal), and pages on (document, page_number), so re-running
  an ingestion job overwrites rather than duplicates.

Text columns carry CHECKs on their allowed values instead of PostgreSQL enums: adding a status later
is a one-line constraint change rather than an ``ALTER TYPE``.

Documents arrive through two doors (ADR 018): an upload (``source = 'upload'``, no public
address) or a filing the worker fetched from BSE (``source = 'bse'``, with the https address on
www.bseindia.com it came from, unique so one filing is fetched once).

AMENDED BEFORE ANY LASTING DATABASE HELD IT (P9c): the source columns and the two filing job kinds
were added to this migration rather than a new one. Production databases are destroyed after every
session (ADR 015), so none holds the earlier shape. A local database that ran the first version:
``alembic downgrade 0003`` then ``alembic upgrade head`` (it loses only P9 test rows).

The embedding column arrives with P10's migration, when there is something to put in it.

Production migrations are forward-only. ``downgrade`` exists so local development and tests can
prove this migration is reversible (up, down, up).
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "status IN ('pending', 'processing', 'completed', 'failed')"
SHA256 = "~ '^[0-9a-f]{64}$'"


def _timestamp(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        # Who uploaded it. Kept if that user is later deleted, just no longer attributed.
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        # Where the original file is kept (BlobStore). Derived from sha256; never served to users.
        sa.Column("blob_key", sa.Text(), nullable=False),
        # Which door the document came through, and for a fetched filing, its public address:
        # what the UI links to, since the stored file itself is never served (ADR 007).
        sa.Column("source", sa.Text(), server_default="upload", nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], name="fk_documents_stock_id_stocks"),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name="fk_documents_uploaded_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("sha256", name="uq_documents_sha256"),
        sa.CheckConstraint(f"sha256 {SHA256}", name="ck_documents_sha256_hex"),
        sa.CheckConstraint(
            "blob_key = 'documents/' || sha256 || '.pdf'", name="ck_documents_blob_key_from_sha256"
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name="ck_documents_title_length"
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_documents_size_positive"),
        sa.CheckConstraint(STATUSES, name="ck_documents_status"),
        sa.CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_documents_page_count"),
        sa.CheckConstraint(
            # IS NOT NULL is not redundant: `NULL LIKE ...` is unknown, and a CHECK accepts unknown,
            # so without it a 'bse' document with no address would pass (a test caught that).
            "(source = 'upload' AND source_url IS NULL) OR "
            "(source = 'bse' AND source_url IS NOT NULL "
            "AND source_url LIKE 'https://www.bseindia.com/%')",
            name="ck_documents_source",
        ),
    )
    # One official address, one document: a filing is fetched once, whatever the timer does.
    op.create_index(
        "uq_documents_source_url",
        "documents",
        ["source_url"],
        unique=True,
        postgresql_where=sa.text("source_url IS NOT NULL"),
    )
    # Serves "this stock's documents, newest first" (the list endpoint's cursor walks id downwards).
    op.create_index("ix_documents_stock_id_id", "documents", ["stock_id", "id"])

    op.create_table(
        "document_pages",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        # The whole page's text, kept so P11's validator can check a quote against its cited page.
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "page_number", name="pk_document_pages"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_pages_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("page_number >= 1", name="ck_document_pages_page_number"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        # Position within the document, from 0. With document_id, the natural identity of a chunk.
        sa.Column("ordinal", sa.Integer(), nullable=False),
        # The page the chunk's text comes from: what a citation will point at.
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # SHA-256 of the text: P10 reuses an existing embedding for identical text.
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_id_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
        sa.CheckConstraint("page_number >= 1", name="ck_chunks_page_number"),
        sa.CheckConstraint(f"content_hash {SHA256}", name="ck_chunks_content_hash_hex"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # What makes two jobs "the same work", e.g. ingest_document:42.
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        # Not before this time: how a retry waits out its backoff.
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # The lease: a processing job whose lease has passed is presumed abandoned by a dead worker.
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.CheckConstraint(
            "kind IN ('ingest_document', 'poll_feed', 'discover_filings', 'fetch_filing')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(STATUSES, name="ck_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_jobs_max_attempts"),
    )
    op.create_index(
        "uq_jobs_live_dedupe_key",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    # Serves the worker's claim query: the next runnable job.
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["status", "run_after"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_table("jobs")  # its indexes go with it
    op.drop_table("chunks")
    op.drop_table("document_pages")
    op.drop_table("documents")
