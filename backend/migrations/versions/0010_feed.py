"""feed_items and feed_state, and events that may come from a feed item (P15)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29

feed_items: one row per press release the RBI feed showed us (source 'rbi'). Two UNIQUE
constraints make the poll idempotent, whatever runs it and however often: the same canonical URL
is one item, and so is the same title hash (the same release re-published under a new address).
``is_fixture`` marks the synthetic items of FEED_MODE=fixture, so they can be shown labelled.

feed_state: one row per source holding what the next conditional GET sends back (ETag,
Last-Modified) and how the last poll ended. A row is created by the first poll.

events: an event now cites EITHER a filing (document_id and page_number, as before) OR a feed item
(feed_item_id). The CHECKs say exactly one, and that a filing event still has its page. One event
per (feed item, stock): re-polling cannot duplicate it.

Production migrations are forward-only. ``downgrade`` exists only so local development and tests
can prove this migration reversible: it deletes the feed events (they cannot satisfy the old NOT
NULLs) and everything else this revision added.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE feed_items (
            id bigint GENERATED ALWAYS AS IDENTITY,
            source text NOT NULL,
            canonical_url text NOT NULL,
            title text NOT NULL,
            title_hash text NOT NULL,
            published_at timestamptz,
            summary text NOT NULL,
            is_fixture boolean NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_feed_items PRIMARY KEY (id),
            CONSTRAINT uq_feed_items_source_canonical_url UNIQUE (source, canonical_url),
            CONSTRAINT uq_feed_items_source_title_hash UNIQUE (source, title_hash),
            CONSTRAINT ck_feed_items_source CHECK (source = 'rbi'),
            CONSTRAINT ck_feed_items_title_length CHECK (char_length(title) BETWEEN 1 AND 500),
            CONSTRAINT ck_feed_items_title_hash CHECK (title_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_feed_items_summary_length CHECK (char_length(summary) BETWEEN 0 AND 600)
        )
        """
    )
    # Serves "the newest ten": ordered by date, undated items last.
    op.execute(
        "CREATE INDEX ix_feed_items_recent ON feed_items (published_at DESC NULLS LAST, id DESC)"
    )

    op.execute(
        """
        CREATE TABLE feed_state (
            source text NOT NULL,
            etag text,
            last_modified text,
            last_polled_at timestamptz,
            last_result text,
            CONSTRAINT pk_feed_state PRIMARY KEY (source),
            CONSTRAINT ck_feed_state_source CHECK (source = 'rbi'),
            CONSTRAINT ck_feed_state_last_result CHECK (
                last_result IS NULL OR last_result IN ('ok', 'not_modified', 'failed')
            )
        )
        """
    )

    op.execute("ALTER TABLE events ALTER COLUMN document_id DROP NOT NULL")
    op.execute("ALTER TABLE events ALTER COLUMN page_number DROP NOT NULL")
    op.execute("ALTER TABLE events ADD COLUMN feed_item_id bigint")
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT fk_events_feed_item_id_feed_items "
        "FOREIGN KEY (feed_item_id) REFERENCES feed_items (id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_one_source "
        "CHECK (num_nonnulls(document_id, feed_item_id) = 1)"
    )
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_filing_has_page "
        "CHECK (document_id IS NULL OR page_number IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT uq_events_feed_item_stock "
        "UNIQUE (feed_item_id, stock_id)"
    )


def downgrade() -> None:
    # Feed events exist only locally or in tests until Go-live; they cannot satisfy the old NOT
    # NULLs, so they go first.
    op.execute("DELETE FROM events WHERE feed_item_id IS NOT NULL")
    op.execute("ALTER TABLE events DROP CONSTRAINT uq_events_feed_item_stock")
    op.execute("ALTER TABLE events DROP CONSTRAINT ck_events_filing_has_page")
    op.execute("ALTER TABLE events DROP CONSTRAINT ck_events_one_source")
    op.execute("ALTER TABLE events DROP CONSTRAINT fk_events_feed_item_id_feed_items")
    op.execute("ALTER TABLE events DROP COLUMN feed_item_id")
    op.execute("ALTER TABLE events ALTER COLUMN page_number SET NOT NULL")
    op.execute("ALTER TABLE events ALTER COLUMN document_id SET NOT NULL")
    op.execute("DROP TABLE feed_state")
    op.execute("DROP TABLE feed_items")
