"""Static checks on the migration scripts. No database needed, so these run in every quick run."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import Settings
from tests.helpers import build_settings

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def scripts() -> ScriptDirectory:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(config)


def test_there_is_exactly_one_head_and_it_is_the_chat_tables_migration(
    scripts: ScriptDirectory,
) -> None:
    """Two heads would mean two people branched the schema history; fail before it reaches CI."""
    assert scripts.get_heads() == ["0012"]


def test_history_is_a_single_straight_line_from_an_empty_database(scripts: ScriptDirectory) -> None:
    revisions = list(scripts.walk_revisions())  # newest first
    assert [r.revision for r in revisions] == [
        "0012", "0011", "0010", "0009", "0008", "0007", "0006", "0005", "0004", "0003",
        "0002", "0001",
    ]  # fmt: skip
    assert revisions[0].down_revision == "0011"  # the chat table column extends messages (0008)
    assert revisions[1].down_revision == "0010"  # prices belong to stocks
    assert revisions[2].down_revision == "0009"  # feed items extend events (0007)
    assert revisions[3].down_revision == "0008"  # the investor profile belongs to a user
    assert revisions[4].down_revision == "0007"  # conversations belong to users
    assert revisions[5].down_revision == "0006"  # facts and events cite documents
    assert revisions[6].down_revision == "0005"  # fingerprints of chunk texts
    assert revisions[7].down_revision == "0004"  # kind and period extend documents
    assert revisions[8].down_revision == "0003"  # documents need stocks and users
    assert revisions[9].down_revision == "0002"  # follows need users (and stocks)
    assert revisions[10].down_revision == "0001"  # the auth tables build on the stocks migration
    assert revisions[11].down_revision is None  # the very first migration starts from nothing


def test_every_migration_can_be_reversed(scripts: ScriptDirectory) -> None:
    """`up, down, up` is only possible if each script defines both directions."""
    for revision in scripts.walk_revisions():
        assert callable(revision.module.upgrade), revision.revision
        assert callable(revision.module.downgrade), revision.revision


def test_the_migration_url_is_not_part_of_application_settings() -> None:
    """The privileged URL must be unreachable from the API and worker's configuration object."""
    assert "migration_database_url" not in Settings.model_fields
    settings = build_settings()
    assert not hasattr(settings, "migration_database_url")
    assert "MIGRATION" not in repr(settings).upper()


def test_alembic_ini_holds_no_credentials() -> None:
    text = (BACKEND / "alembic.ini").read_text(encoding="utf-8").lower()
    assert "postgresql" not in text  # a URL, and so a password, must never be committed here
    assert "password" not in text
