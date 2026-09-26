"""embeddings: one fingerprint per (model, chunk text), and the embed_document job kind (P10)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26

Keyed by the chunk text's SHA-256 (chunks.content_hash), not by the chunk: the same paragraph in two
filings (a standard disclaimer, say) is embedded and paid for once, and a document read again keeps
its fingerprints. The model is part of the key, so fingerprints of two models are never compared:
after a model change the new model's rows are simply missing, and the worker makes them.

No vector index: exact search over some 16,000 rows (about 5,000 per stock) takes milliseconds.
An approximate index (HNSW) is what millions of rows would need; see ADR 019.

input_tokens is what AWS billed for the text. Summed, it is the exact spend so far, and the
worker's spending cap (EMBEDDING_TOKEN_BUDGET) is checked against it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA256 = "~ '^[0-9a-f]{64}$'"
OLD_KINDS = "kind IN ('ingest_document', 'poll_feed', 'discover_filings', 'fetch_filing')"
NEW_KINDS = (
    "kind IN ('ingest_document', 'poll_feed', 'discover_filings', 'fetch_filing', 'embed_document')"
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE embeddings (
            model text NOT NULL,
            content_hash text NOT NULL,
            embedding vector(1024) NOT NULL,
            input_tokens integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_embeddings PRIMARY KEY (model, content_hash),
            CONSTRAINT ck_embeddings_model CHECK (char_length(model) BETWEEN 1 AND 200),
            CONSTRAINT ck_embeddings_content_hash_hex CHECK (content_hash {SHA256}),
            CONSTRAINT ck_embeddings_input_tokens CHECK (input_tokens >= 0)
        )
        """
    )
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", NEW_KINDS)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'embed_document'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", OLD_KINDS)
    op.drop_table("embeddings")
