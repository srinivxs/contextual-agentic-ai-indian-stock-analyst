"""What the stack does when something is wrong: it stops early, says what, and leaks nothing.

Each test builds its own short-lived project (the image layers are cached after the first build).
"""

from pathlib import Path

from stack import Stack


def test_a_failing_migration_stops_the_api_and_web_from_ever_starting(
    daemon: None, tmp_path: Path
) -> None:
    override = tmp_path / "fail-migrate.yml"
    override.write_text(
        'services:\n  migrate:\n    command: ["sh", "-c", "echo simulated failure >&2; exit 3"]\n',
        encoding="utf-8",
        newline="\n",
    )
    failing = Stack(tmp_path, override_files=[override])
    assert "migrate" in failing.services(), "the `app` profile has no migrate service yet"
    try:
        result = failing.up("web")
        assert result.returncode != 0
        assert failing.inspect("migrate", "{{json .State.ExitCode}}") == 3
        assert failing.never_started("api"), "the api started although migrate failed"
        assert failing.never_started("web"), "web started although migrate failed"
    finally:
        failing.down()


def test_a_missing_google_secret_stops_the_api_naming_only_the_setting(
    daemon: None, tmp_path: Path
) -> None:
    """Required app secrets use `${VAR:-}` (see test_compose_static), so the api itself refuses."""
    broken = Stack(tmp_path, drop=["GOOGLE_CLIENT_SECRET"])
    assert "api" in broken.services(), "the `app` profile has no api service yet"
    try:
        result = broken.up("web")
        assert result.returncode != 0
        assert broken.never_started("web"), "web started although the api could not"

        logs = broken.compose("logs", "api", check=False)
        text = (logs.stdout + logs.stderr).lower()
        assert "google_client_secret" in text
        for value in (broken.session_secret, broken.app_password, broken.admin_password):
            assert value.lower() not in text, "a secret value was written to the logs"
    finally:
        broken.down()


def test_the_plain_database_command_still_works_with_no_app_settings_at_all(
    daemon: None, tmp_path: Path
) -> None:
    """`docker compose up -d --wait` (no profile) must keep starting only the database."""
    bare = Stack(
        tmp_path,
        drop=["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "WEB_PORT"],
    )
    try:
        result = bare.up(profile=False)
        assert result.returncode == 0, (result.stdout + result.stderr)[-2000:]
        running = bare.compose("ps", "--services", profile=False).stdout.split()
        assert running == ["db"]
        assert bare.psql("select 1") == "1"
    finally:
        bare.down()
