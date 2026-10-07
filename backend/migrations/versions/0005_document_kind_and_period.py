"""documents.kind and documents.period: what a filing is, and the period it covers (P9d)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23

The Documents page groups filings (earnings-call transcripts, investor presentations, annual
reports, announcements) and orders each group by period. Until now that was only inside the title.

WHY A SECOND MIGRATION IN P9 (the convention is one per phase): 0004 had already been applied, with
real rows, to a working database when this was needed. Forward-only beats one-per-phase: editing an
applied migration would leave that database with a schema no migration describes.

The backfill reads the title the worker has always written, "<SYMBOL> <kind words>, <period>", so
existing filings are grouped correctly without being fetched again. Uploads keep NULL: they have no
kind or period of their own.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "kind IN ('transcript', 'presentation', 'annual_report', 'announcement')"

# kind words as the worker writes them in titles (app/filings.py KIND_WORDS).
BACKFILL = """
    UPDATE documents SET
        kind = CASE
            WHEN title ~ '^[A-Z0-9&-]+ earnings call transcript, ' THEN 'transcript'
            WHEN title ~ '^[A-Z0-9&-]+ investor presentation, ' THEN 'presentation'
            WHEN title ~ '^[A-Z0-9&-]+ annual report, ' THEN 'annual_report'
            WHEN title ~ '^[A-Z0-9&-]+ announcement, ' THEN 'announcement'
        END,
        period = substring(title from '^[^,]*, (.*)$')
    WHERE source = 'bse'
"""


def upgrade() -> None:
    op.add_column("documents", sa.Column("kind", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("period", sa.Text(), nullable=True))
    op.execute(BACKFILL)
    op.create_check_constraint("ck_documents_kind", "documents", f"kind IS NULL OR {KINDS}")
    op.create_check_constraint(
        "ck_documents_period_length", "documents", "period IS NULL OR char_length(period) <= 200"
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_period_length", "documents", type_="check")
    op.drop_constraint("ck_documents_kind", "documents", type_="check")
    op.drop_column("documents", "period")
    op.drop_column("documents", "kind")
