"""screener_ratios and data_refreshes: the Fundamentals card, and "Update data" once an hour

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-30

screener_ratios: one row per stock, the "top ratios" list at the top of its screener.in company
page (market cap, current price, stock P/E, book value, dividend yield, ROCE, ROE, face value),
read by code from the page the worker already fetches once a day (ADR 018, ADR 020) and replaced
on each read. A missing item is NULL, never zero.

data_refreshes: when someone pressed "Update data". The api allows the next press an hour after
the last one, for everyone together (the owner's rule: it protects the sources). The user goes
with the row as a plain reference and is set to NULL if the account is deleted.

Production migrations are forward-only. ``downgrade`` exists only so local development and tests
can prove this migration reversible.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE screener_ratios (
            stock_id bigint PRIMARY KEY REFERENCES stocks (id) ON DELETE CASCADE,
            market_cap_crore numeric,
            current_price numeric,
            stock_pe numeric,
            book_value numeric,
            dividend_yield_percent numeric,
            roce_percent numeric,
            roe_percent numeric,
            face_value numeric,
            source_url text NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE data_refreshes (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            requested_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_data_refreshes_requested_at ON data_refreshes (requested_at)")


def downgrade() -> None:
    op.execute("DROP TABLE data_refreshes")
    op.execute("DROP TABLE screener_ratios")
