"""user_follows: which user follows which stock

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21

One row per (user, stock) pair. The pair is the primary key, so following the same stock twice is
impossible at the database level and the follow endpoint can rely on ``ON CONFLICT DO NOTHING``
(ADR 013). There is no surrogate id: nothing refers to a follow.

* Deleting a user removes their follows (``ON DELETE CASCADE``), like their sessions.
* Deleting a stock that someone follows is refused (the default, ``NO ACTION``). The universe is
  fixed (ADR 007); removing a stock must be a deliberate migration, not a side effect that silently
  erases people's follows.

Production migrations are forward-only. ``downgrade`` exists so local development and tests can
prove this migration is reversible (up, down, up).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_follows",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # The pair is the identity, and its index serves "what does this user follow?".
        sa.PrimaryKeyConstraint("user_id", "stock_id", name="pk_user_follows"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_follows_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["stock_id"], ["stocks.id"], name="fk_user_follows_stock_id_stocks"
        ),
    )


def downgrade() -> None:
    op.drop_table("user_follows")  # its constraints go with it
