"""facts, events and extraction_calls, and the extract_document job kind (P11, ADR 020)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27

facts: one row per figure, from one of two sources, each with its own citation, enforced here:
  - 'filing': read by the LLM from a stored filing and accepted by the deterministic validator;
    cited by document, page and a verbatim quote.
  - 'screener': parsed by code from screener.in's fundamentals table (the owner's P11 decision);
    cited by the page URL and the section, row and column of the figure.
  Money keeps the currency it was reported in (the owner's decision: never converted), so the unit
  and the currency must agree: INR_CRORE and INR_PER_SHARE are INR, USD_MILLION and USD_PER_SHARE
  are USD, PERCENT has no currency. Every row is kept; which one is shown is decided on read
  (ADR 020's conflict policy, app/derived.py).

  One filing fact per (document, metric, period, basis, currency): re-running the extraction for a
  document cannot duplicate it. One screener fact per (stock, metric, period, basis): the daily
  read updates it in place.

events: what happened (a fixed list of types), with sentiment, impact, the page and a quote. One
per (document, event type). Rolling sentiment is computed on read (ADR 009).

extraction_calls: one row per LLM call, with its tokens: the exact spend so far, the spending cap's
input, and what lets a stopped job skip the windows it has already paid for. Rejections are kept
as codes only (never document text).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRICS = (
    "'revenue_from_operations', 'net_interest_income', 'net_profit', 'total_borrowings', "
    "'total_equity', 'dividend_per_share', 'eps_basic', 'return_on_equity', "
    "'net_interest_margin', 'gross_npa_ratio'"
)
EVENT_TYPES = (
    "'earnings_results', 'guidance_outlook', 'dividend', 'credit_rating', "
    "'debt_or_capital_raise', 'merger_acquisition', 'management_change', 'regulatory_legal', "
    "'order_win_partnership', 'investor_meeting', 'other'"
)
OLD_KINDS = (
    "kind IN ('ingest_document', 'poll_feed', 'discover_filings', 'fetch_filing', 'embed_document')"
)
NEW_KINDS = (
    "kind IN ('ingest_document', 'poll_feed', 'discover_filings', 'fetch_filing', "
    "'embed_document', 'extract_document')"
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE facts (
            id bigint GENERATED ALWAYS AS IDENTITY,
            stock_id bigint NOT NULL,
            source text NOT NULL,
            document_id bigint,
            page_number integer,
            quote text,
            source_url text,
            source_section text,
            source_row text,
            source_column text,
            metric text NOT NULL,
            period text NOT NULL,
            period_end date NOT NULL,
            basis text NOT NULL,
            currency text,
            unit text NOT NULL,
            value numeric(20, 4) NOT NULL,
            reported_text text,
            model text,
            version text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_facts PRIMARY KEY (id),
            CONSTRAINT fk_facts_stock_id_stocks FOREIGN KEY (stock_id) REFERENCES stocks (id),
            CONSTRAINT fk_facts_document_id_documents FOREIGN KEY (document_id)
                REFERENCES documents (id) ON DELETE CASCADE,
            CONSTRAINT ck_facts_citation CHECK (
                (source = 'filing'
                 AND document_id IS NOT NULL AND page_number IS NOT NULL AND quote IS NOT NULL
                 AND source_url IS NULL AND source_section IS NULL AND source_row IS NULL
                 AND source_column IS NULL)
                OR
                (source = 'screener'
                 AND document_id IS NULL AND page_number IS NULL AND quote IS NULL
                 AND source_url IS NOT NULL AND source_url LIKE 'https://www.screener.in/company/%'
                 AND source_section IS NOT NULL AND source_row IS NOT NULL
                 AND source_column IS NOT NULL)
            ),
            CONSTRAINT ck_facts_page_number CHECK (page_number IS NULL OR page_number >= 1),
            CONSTRAINT ck_facts_quote_length CHECK (
                quote IS NULL OR char_length(quote) BETWEEN 1 AND 400
            ),
            CONSTRAINT ck_facts_reported_text_length CHECK (
                reported_text IS NULL OR char_length(reported_text) <= 60
            ),
            CONSTRAINT ck_facts_metric CHECK (metric IN ({METRICS})),
            CONSTRAINT ck_facts_period CHECK (period ~ '^(FY[0-9]{{4}}|Q[1-4]FY[0-9]{{4}})$'),
            CONSTRAINT ck_facts_basis CHECK (
                basis IN ('consolidated', 'standalone', 'unspecified')
            ),
            -- IS NOT DISTINCT FROM, not "=": "NULL = 'INR'" is unknown, and a CHECK passes unknown,
            -- so a rupee amount with no currency would slip through (P9's lesson, caught again).
            CONSTRAINT ck_facts_unit_currency CHECK (
                (unit IN ('INR_CRORE', 'INR_PER_SHARE') AND currency IS NOT DISTINCT FROM 'INR')
                OR (unit IN ('USD_MILLION', 'USD_PER_SHARE')
                    AND currency IS NOT DISTINCT FROM 'USD')
                OR (unit = 'PERCENT' AND currency IS NULL)
            )
        )
        """
    )
    # One filing fact per document, metric, period, basis and currency; one screener fact per
    # stock, metric, period and basis. Partial unique indexes: each applies to its source only.
    op.execute(
        # NULLS NOT DISTINCT: a percentage has no currency, and without it every NULL would count
        # as different, so the same percentage could be stored twice (PostgreSQL 15+).
        "CREATE UNIQUE INDEX uq_facts_filing ON facts "
        "(document_id, metric, period, basis, currency) NULLS NOT DISTINCT "
        "WHERE source = 'filing'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_facts_screener ON facts "
        "(stock_id, metric, period, basis) WHERE source = 'screener'"
    )
    op.execute("CREATE INDEX ix_facts_stock_metric ON facts (stock_id, metric, period_end DESC)")

    op.execute(
        f"""
        CREATE TABLE events (
            id bigint GENERATED ALWAYS AS IDENTITY,
            stock_id bigint NOT NULL,
            document_id bigint NOT NULL,
            page_number integer NOT NULL,
            event_type text NOT NULL,
            sentiment text NOT NULL,
            impact text NOT NULL,
            event_date date NOT NULL,
            date_source text NOT NULL,
            summary text NOT NULL,
            quote text NOT NULL,
            model text,
            version text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_events PRIMARY KEY (id),
            CONSTRAINT fk_events_stock_id_stocks FOREIGN KEY (stock_id) REFERENCES stocks (id),
            CONSTRAINT fk_events_document_id_documents FOREIGN KEY (document_id)
                REFERENCES documents (id) ON DELETE CASCADE,
            CONSTRAINT uq_events_document_type UNIQUE (document_id, event_type),
            CONSTRAINT ck_events_page_number CHECK (page_number >= 1),
            CONSTRAINT ck_events_event_type CHECK (event_type IN ({EVENT_TYPES})),
            CONSTRAINT ck_events_sentiment CHECK (sentiment IN ('negative', 'neutral', 'positive')),
            CONSTRAINT ck_events_impact CHECK (impact IN ('low', 'medium', 'high')),
            CONSTRAINT ck_events_date_source CHECK (date_source IN ('document', 'text', 'fetched')),
            CONSTRAINT ck_events_summary_length CHECK (char_length(summary) BETWEEN 1 AND 200),
            CONSTRAINT ck_events_quote_length CHECK (char_length(quote) BETWEEN 1 AND 400)
        )
        """
    )
    op.execute("CREATE INDEX ix_events_stock_date ON events (stock_id, event_date DESC)")

    op.execute(
        """
        CREATE TABLE extraction_calls (
            document_id bigint NOT NULL,
            pass text NOT NULL,
            first_page integer NOT NULL,
            last_page integer NOT NULL,
            model text NOT NULL,
            version text NOT NULL,
            input_tokens integer NOT NULL,
            output_tokens integer NOT NULL,
            accepted integer NOT NULL DEFAULT 0,
            rejections jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_extraction_calls
                PRIMARY KEY (document_id, pass, first_page, model, version),
            CONSTRAINT fk_extraction_calls_document_id_documents FOREIGN KEY (document_id)
                REFERENCES documents (id) ON DELETE CASCADE,
            CONSTRAINT ck_extraction_calls_pass CHECK (pass IN ('facts', 'events')),
            CONSTRAINT ck_extraction_calls_pages
                CHECK (first_page >= 1 AND last_page >= first_page),
            CONSTRAINT ck_extraction_calls_tokens CHECK (
                input_tokens >= 0 AND output_tokens >= 0 AND accepted >= 0
            )
        )
        """
    )

    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", NEW_KINDS)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'extract_document'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", OLD_KINDS)
    op.drop_table("extraction_calls")
    op.drop_table("events")
    op.drop_table("facts")
