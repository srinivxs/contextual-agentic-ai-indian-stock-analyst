"""prices and price_days, and the sync_prices job kind (ADR 025)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29

prices: one row per stock and trading day, from BSE's daily price file. The PRIMARY KEY
(stock_id, trade_date) is what makes fetching idempotent: the same file read twice, or by two
workers, stores each row once (ON CONFLICT DO NOTHING). Every price is CHECKed positive.
prev_close is BSE's previous close as given on that day (already adjusted for a bonus or split).

price_days: one row per day we tried, so the next run knows what is left. 'fetched' = the file
was read; 'missing' = BSE answered "slow down" or no file yet (attempts counts); 'no_file' = three
strikes, so a weekday holiday and not asked for again.

jobs: the sync_prices kind.

Production migrations are forward-only. ``downgrade`` exists only so local development and tests
can prove this migration reversible.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "kind IN ('ingest_document', 'poll_feed', 'discover_filings', 'fetch_filing', "
    "'embed_document', 'extract_document')"
)
NEW_KINDS = (
    "kind IN ('ingest_document', 'poll_feed', 'discover_filings', 'fetch_filing', "
    "'embed_document', 'extract_document', 'sync_prices')"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE prices (
            stock_id bigint NOT NULL,
            trade_date date NOT NULL,
            open numeric(14, 2) NOT NULL,
            high numeric(14, 2) NOT NULL,
            low numeric(14, 2) NOT NULL,
            close numeric(14, 2) NOT NULL,
            prev_close numeric(14, 2) NOT NULL,
            volume bigint NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_prices PRIMARY KEY (stock_id, trade_date),
            CONSTRAINT fk_prices_stock_id_stocks FOREIGN KEY (stock_id) REFERENCES stocks (id),
            CONSTRAINT ck_prices_open CHECK (open > 0),
            CONSTRAINT ck_prices_high CHECK (high > 0),
            CONSTRAINT ck_prices_low CHECK (low > 0),
            CONSTRAINT ck_prices_close CHECK (close > 0),
            CONSTRAINT ck_prices_prev_close CHECK (prev_close > 0),
            CONSTRAINT ck_prices_volume CHECK (volume >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE price_days (
            trade_date date NOT NULL,
            status text NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_price_days PRIMARY KEY (trade_date),
            CONSTRAINT ck_price_days_status CHECK (status IN ('fetched', 'missing', 'no_file')),
            CONSTRAINT ck_price_days_attempts CHECK (attempts >= 0)
        )
        """
    )
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", NEW_KINDS)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'sync_prices'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", OLD_KINDS)
    op.execute("DROP TABLE price_days")
    op.execute("DROP TABLE prices")
