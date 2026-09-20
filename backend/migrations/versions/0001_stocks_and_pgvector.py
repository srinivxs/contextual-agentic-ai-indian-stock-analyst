"""stocks table, its three seeded rows, and the pgvector extension

Revision ID: 0001
Revises:
Create Date: 2026-09-20

Everything here is deterministic and self-contained: a fresh database is reproduced by running this
file alone, with no data file and no network access.

Seed sources (verified by the project owner and recorded in ADR 011):
  * bse_code: RELIANCE 500325 and HDFCBANK 500180 from each company's own filing to BSE and NSE;
    TCS 532540 confirmed on BSE's own stock page.
  * sector: the "Industry" column of NSE's Nifty 50 constituent list, used verbatim.
  * name: the full legal company name as listed on NSE.
  * is_financial: OUR classification, not an exchange fact (ADR 009): true only for the bank.

Production migrations are forward-only. ``downgrade`` exists so local development and tests can
prove this migration is reversible (up, down, up).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The universe is exactly these three stocks (ADR 007). Adding a stock later is a new migration.
STOCKS: list[dict[str, str | bool]] = [
    {
        "symbol": "RELIANCE",
        "name": "Reliance Industries Limited",
        "bse_code": "500325",
        "sector": "Oil Gas & Consumable Fuels",
        "is_financial": False,
    },
    {
        "symbol": "TCS",
        "name": "Tata Consultancy Services Limited",
        "bse_code": "532540",
        "sector": "Information Technology",
        "is_financial": False,
    },
    {
        "symbol": "HDFCBANK",
        "name": "HDFC Bank Limited",
        "bse_code": "500180",
        "sector": "Financial Services",
        "is_financial": True,
    },
]

INSERT_STOCK = sa.text(
    "INSERT INTO stocks (symbol, name, bse_code, sector, is_financial) "
    "VALUES (:symbol, :name, :bse_code, :sector, :is_financial) "
    "ON CONFLICT (symbol) DO NOTHING"
)


def upgrade() -> None:
    # Needs a privileged role, so it lives here (run by the migration role), never in app code.
    # No table uses the type yet: the embedding column arrives with `chunks` in P10.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "stocks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("bse_code", sa.Text(), nullable=False),
        sa.Column("sector", sa.Text(), nullable=False),
        sa.Column("is_financial", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocks"),
        sa.UniqueConstraint("symbol", name="uq_stocks_symbol"),
        sa.UniqueConstraint("bse_code", name="uq_stocks_bse_code"),
        # Same pattern the API validates ticker path parameters against.
        sa.CheckConstraint("symbol ~ '^[A-Z0-9&-]{1,20}$'", name="ck_stocks_symbol_format"),
        # A BSE scrip code is six digits; kept as text because it is an identifier, not a number.
        sa.CheckConstraint("bse_code ~ '^[0-9]{6}$'", name="ck_stocks_bse_code_format"),
    )

    # Parameterised inserts (no string-built SQL). ON CONFLICT makes a repeated seed a no-op.
    connection = op.get_bind()
    for stock in STOCKS:
        connection.execute(INSERT_STOCK, stock)


def downgrade() -> None:
    op.drop_table("stocks")
    op.execute("DROP EXTENSION IF EXISTS vector")
