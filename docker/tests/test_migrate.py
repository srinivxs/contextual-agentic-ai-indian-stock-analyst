"""The `migrate` command: the same image with another command, the only place the admin URL goes.

Runs against a throwaway PostgreSQL + pgvector on a private network with the real role bootstrap, so
it exercises the same two-role setup as development, entirely inside Docker.
"""

import re
import subprocess

from conftest import ThrowawayDatabase
from helpers import (
    BACKEND,
    FAKE_ENV,
    LABEL,
    docker,
    http_status,
    inspect_json,
    output,
    running_api,
    wait_healthy,
)


def migration_head() -> str:
    """The newest revision, read from the migration files themselves (so it never goes stale)."""
    revisions: dict[str, str | None] = {}
    for path in (BACKEND / "migrations" / "versions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = "(\w+)"', text, re.MULTILINE)
        parent = re.search(r'^down_revision: str \| None = (None|"(\w+)")', text, re.MULTILINE)
        assert revision is not None, path
        assert parent is not None, path
        revisions[revision.group(1)] = parent.group(2)
    heads = set(revisions) - {p for p in revisions.values() if p}
    assert len(heads) == 1
    return heads.pop()


def migrate(image: str, db: ThrowawayDatabase, url: str | None) -> subprocess.CompletedProcess[str]:
    """Run `alembic upgrade head` from the image. A None url means: no migration URL at all."""
    args = ["run", "--rm", "--label", LABEL, "--network", db.network]
    if url is not None:
        args += ["-e", f"MIGRATION_DATABASE_URL={url}"]
    return docker(*args, image, "alembic", "upgrade", "head", check=False, timeout=180)


def test_migrate_brings_an_empty_database_to_the_newest_revision(
    image: str, database: ThrowawayDatabase
) -> None:
    result = migrate(image, database, database.admin_url)

    assert result.returncode == 0, output(result)
    assert database.query("select version_num from alembic_version") == migration_head()
    tables = database.query(
        "select string_agg(tablename, ',') from pg_tables where schemaname = 'public'"
    )
    assert {"stocks", "users", "sessions", "user_follows"} <= set(tables.split(","))
    assert database.query("select count(*) from pg_extension where extname = 'vector'") == "1"
    assert database.query("select count(*) from stocks") == "3"


def test_running_it_again_changes_nothing(image: str, database: ThrowawayDatabase) -> None:
    for _ in range(2):
        assert migrate(image, database, database.admin_url).returncode == 0
    assert database.query("select version_num from alembic_version") == migration_head()
    assert database.query("select count(*) from stocks") == "3"


def test_a_wrong_admin_password_fails_the_migration_without_echoing_any_password(
    image: str, database: ThrowawayDatabase
) -> None:
    wrong = database.admin_url.replace(database.admin_password, "wrong-password-p6")
    result = migrate(image, database, wrong)

    assert result.returncode != 0
    text = output(result)
    assert "wrong-password-p6" not in text
    assert database.admin_password not in text


def test_without_the_migration_url_it_refuses_and_names_only_the_variable(
    image: str, database: ThrowawayDatabase
) -> None:
    """There is no .env.migration fallback inside the image: the URL comes from the environment."""
    result = migrate(image, database, None)

    assert result.returncode != 0
    text = output(result)
    assert "MIGRATION_DATABASE_URL" in text
    assert database.admin_password not in text


def test_the_api_started_with_the_runtime_role_is_ready_and_never_holds_admin_credentials(
    image: str, database: ThrowawayDatabase
) -> None:
    assert migrate(image, database, database.admin_url).returncode == 0
    env = {**FAKE_ENV, "DATABASE_URL": database.app_url}

    with running_api(image, env, network=database.network) as name:
        wait_healthy(name)
        # The runtime role can reach the migrated schema: ready, not merely alive.
        assert http_status(name, "/api/readyz") == 200

        environment = inspect_json(name, "{{json .Config.Env}}")
        assert isinstance(environment, list)
        flat = "\n".join(environment)
        assert "MIGRATION_DATABASE_URL" not in flat
        assert database.admin_password not in flat
        assert database.admin_user not in flat
