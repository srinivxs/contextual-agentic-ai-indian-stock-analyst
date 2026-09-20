"""The `stocks` table and its seed data, at the latest migration, on the real database.

Sources for the expected values (kept out of the migration's logic, written here as a second copy so
a typo in either place fails a test):
  * BSE scrip codes: RELIANCE 500325 and HDFCBANK 500180 from the companies' own filings to BSE/NSE;
    TCS 532540 confirmed on BSE's own stock page by the project owner.
  * Sector: the "Industry" column of NSE's Nifty 50 list (ind_nifty50list.csv), used verbatim.
  * Names: the full legal names as listed on NSE.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.usefixtures("migrated_db")

EXPECTED = [
    ("RELIANCE", "Reliance Industries Limited", "500325", "Oil Gas & Consumable Fuels", False),
    ("TCS", "Tata Consultancy Services Limited", "532540", "Information Technology", False),
    ("HDFCBANK", "HDFC Bank Limited", "500180", "Financial Services", True),
]

CONSTRAINTS = """
    SELECT conname FROM pg_constraint WHERE conrelid = 'public.stocks'::regclass ORDER BY conname
"""

COLUMNS = """
    SELECT column_name, data_type, is_nullable, is_identity
    FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'stocks'
    ORDER BY ordinal_position
"""


async def test_exactly_the_three_project_stocks_with_their_verified_metadata(
    app_engine: AsyncEngine,
) -> None:
    async with app_engine.connect() as connection:
        result = await connection.execute(
            text("SELECT symbol, name, bse_code, sector, is_financial FROM stocks ORDER BY id")
        )
        rows = [tuple(row) for row in result]
    assert rows == EXPECTED  # exactly three, in seed order, nothing extra and nothing missing


async def test_only_hdfc_bank_is_marked_financial(app_engine: AsyncEngine) -> None:
    """ADR 009: debt-to-equity is 'not applicable' for banks, decided by this flag."""
    async with app_engine.connect() as connection:
        result = await connection.execute(text("SELECT symbol FROM stocks WHERE is_financial"))
        assert result.scalars().all() == ["HDFCBANK"]


async def test_the_table_has_the_agreed_columns_and_types(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        columns = [tuple(row) for row in await connection.execute(text(COLUMNS))]
    assert columns == [
        ("id", "bigint", "NO", "YES"),  # identity bigint, per the project's database rules
        ("symbol", "text", "NO", "NO"),
        ("name", "text", "NO", "NO"),
        ("bse_code", "text", "NO", "NO"),
        ("sector", "text", "NO", "NO"),
        ("is_financial", "boolean", "NO", "NO"),
        ("created_at", "timestamp with time zone", "NO", "NO"),  # UTC timestamptz everywhere
    ]


async def test_constraints_have_explicit_names(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        names = (await connection.execute(text(CONSTRAINTS))).scalars().all()
    assert names == [
        "ck_stocks_bse_code_format",
        "ck_stocks_symbol_format",
        "pk_stocks",
        "uq_stocks_bse_code",
        "uq_stocks_symbol",
    ]


async def test_created_at_defaults_to_now_and_is_timezone_aware(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        created = (
            await connection.execute(text("SELECT created_at FROM stocks WHERE symbol = 'TCS'"))
        ).scalar_one()
    assert created.tzinfo is not None


@pytest.mark.parametrize(
    ("symbol", "bse_code"),
    [
        pytest.param("RELIANCE", "111111", id="duplicate-symbol"),
        pytest.param("NEWCO", "500325", id="duplicate-bse-code"),
        pytest.param("bad symbol!", "222222", id="symbol-outside-the-ticker-pattern"),
        pytest.param("A" * 21, "333333", id="symbol-longer-than-20"),
        pytest.param("NEWCO", "12AB56", id="bse-code-not-digits"),
        pytest.param("NEWCO", "12345", id="bse-code-too-short"),
    ],
)
async def test_bad_or_duplicate_rows_are_rejected_by_the_database(
    admin_engine: AsyncEngine, symbol: str, bse_code: str
) -> None:
    async with admin_engine.connect() as connection:
        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    "INSERT INTO stocks (symbol, name, bse_code, sector) "
                    "VALUES (:symbol, 'Some Co', :bse_code, 'Services')"
                ),
                {"symbol": symbol, "bse_code": bse_code},
            )
    async with admin_engine.connect() as connection:
        count = (await connection.execute(text("SELECT count(*) FROM stocks"))).scalar_one()
    assert count == 3


def load_migration() -> ModuleType:
    """Import the real migration file (its name starts with a digit, so importlib by path)."""
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0001_stocks_and_pgvector.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0001", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_seed_values_written_in_the_migration_match_the_verified_metadata() -> None:
    """A second, independent copy of the values: a typo in either file fails here."""
    stocks = load_migration().STOCKS
    assert [
        (s["symbol"], s["name"], s["bse_code"], s["sector"], s["is_financial"]) for s in stocks
    ] == EXPECTED


async def test_running_the_migrations_own_seed_statement_again_creates_no_duplicates(
    admin_engine: AsyncEngine,
) -> None:
    """The migration's own INSERT ... ON CONFLICT DO NOTHING and rows, run twice more."""
    migration = load_migration()
    async with admin_engine.begin() as connection:
        for _ in range(2):
            for stock in migration.STOCKS:
                await connection.execute(migration.INSERT_STOCK, stock)
        count = (await connection.execute(text("SELECT count(*) FROM stocks"))).scalar_one()
    assert count == 3


async def test_reseeding_with_on_conflict_do_nothing_creates_no_duplicates(
    admin_engine: AsyncEngine,
) -> None:
    """The unique constraints are what make a repeated seed safe, not luck."""
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO stocks (symbol, name, bse_code, sector) VALUES "
                "('TCS', 'Tata Consultancy Services Limited', '532540', 'Information Technology') "
                "ON CONFLICT (symbol) DO NOTHING"
            )
        )
        count = (await connection.execute(text("SELECT count(*) FROM stocks"))).scalar_one()
    assert count == 3


async def test_the_runtime_role_can_read_stocks_but_cannot_change_their_structure(
    app_engine: AsyncEngine,
) -> None:
    async with app_engine.connect() as connection:
        count = (await connection.execute(text("SELECT count(*) FROM stocks"))).scalar_one()
        assert count == 3
    for statement in (
        "ALTER TABLE stocks ADD COLUMN sneaky int",
        "DROP TABLE stocks",
        "TRUNCATE stocks",
        "CREATE INDEX ix_sneaky ON stocks (name)",
    ):
        async with app_engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text(statement))
