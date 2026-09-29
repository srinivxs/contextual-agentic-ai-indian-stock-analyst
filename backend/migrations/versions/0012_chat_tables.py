"""messages.data_table: the year-by-year table under an answer (chat redesign)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29

data_table: NULL for most messages. An answer about one stock's profit or revenue may carry a small
table built by code from stored screener.in figures: {"title", "columns", "rows", "source":
{"label", "url"}}. Only the assistant's replies may have one, so a question can never carry a table.

Production migrations are forward-only. ``downgrade`` exists only so local development and tests
can prove this migration reversible.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN data_table jsonb")
    op.execute(
        "ALTER TABLE messages ADD CONSTRAINT ck_messages_data_table_reply "
        "CHECK (data_table IS NULL OR role = 'assistant')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP CONSTRAINT ck_messages_data_table_reply")
    op.execute("ALTER TABLE messages DROP COLUMN data_table")
