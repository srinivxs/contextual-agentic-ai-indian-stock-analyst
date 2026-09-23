"""Fixtures for the container tests.

These tests build and run real containers, so they are deliberately NOT part of the normal
`backend` test run. See README.md for how to run them. They fail, never skip, when Docker is not
available.
"""

import re
import secrets
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from helpers import (
    BACKEND,
    FRONTEND,
    INIT_SCRIPTS,
    LABEL,
    REPO,
    RUN_ID,
    container_name,
    docker,
    wait_for,
)
from stack import Stack


@pytest.fixture(scope="session")
def daemon() -> None:
    probe = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, check=False, timeout=60
    )
    assert probe.returncode == 0, (
        "The Docker daemon is not reachable. Start Docker Desktop (these tests fail, never skip)."
    )


@pytest.fixture(scope="session")
def image(daemon: None) -> Iterator[str]:
    """The backend image, built once from the real Dockerfile in the real backend/ context."""
    tag = f"stock-analyst-api-p6test:{RUN_ID}"
    docker("build", "--label", LABEL, "-t", tag, str(BACKEND))
    yield tag
    docker("rmi", "-f", tag, check=False)


@dataclass(frozen=True)
class ThrowawayDatabase:
    network: str
    host: str  # the database container's name, which is its hostname on the private network
    name: str
    admin_user: str
    admin_password: str
    app_user: str
    app_password: str

    @property
    def admin_url(self) -> str:
        return f"postgresql+asyncpg://{self.admin_user}:{self.admin_password}@{self.host}:5432/{self.name}"

    @property
    def app_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.app_user}:{self.app_password}@{self.host}:5432/{self.name}"
        )

    def query(self, sql: str) -> str:
        result = docker(
            "exec", self.host, "psql", "-U", self.admin_user, "-d", self.name, "-Atc", sql
        )
        return result.stdout.strip()


def _pgvector_image() -> str:
    """The exact database image Compose uses, read from docker-compose.yml so they cannot drift."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"image:\s*(pgvector/pgvector:\S+)", compose)
    assert match, "docker-compose.yml no longer names a pgvector image"
    return match.group(1)


@pytest.fixture(scope="module")
def database(daemon: None) -> Iterator[ThrowawayDatabase]:
    """A throwaway PostgreSQL + pgvector on a private network, with the real role bootstrap.

    Random passwords, no published port, no named volume: it cannot collide with, or reach, the
    development database.
    """
    db = ThrowawayDatabase(
        network=f"p6a-{RUN_ID}-net-{secrets.token_hex(3)}",
        host=container_name("db"),
        name="p6_stock",
        admin_user="p6_admin",
        admin_password=secrets.token_hex(12),
        app_user="p6_app",
        app_password=secrets.token_hex(12),
    )
    docker("network", "create", "--label", LABEL, db.network)
    docker(
        "run",
        "-d",
        "--name",
        db.host,
        "--label",
        LABEL,
        "--network",
        db.network,
        "-e",
        f"POSTGRES_USER={db.admin_user}",
        "-e",
        f"POSTGRES_PASSWORD={db.admin_password}",
        "-e",
        f"POSTGRES_DB={db.name}",
        "-e",
        f"APP_DB_USER={db.app_user}",
        "-e",
        f"APP_DB_PASSWORD={db.app_password}",
        "-v",
        f"{INIT_SCRIPTS}:/docker-entrypoint-initdb.d:ro",
        _pgvector_image(),
    )
    try:
        # Over TCP: the image also runs a temporary socket-only server while it executes the init
        # scripts, and only the real server listens on 127.0.0.1.
        wait_for(
            lambda: (
                docker(
                    "exec",
                    db.host,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    db.admin_user,
                    "-d",
                    db.name,
                    check=False,
                ).returncode
                == 0
            ),
            "the test database to accept connections",
            timeout=120,
        )
        yield db
    finally:
        docker("rm", "-f", "-v", db.host, check=False)
        docker("network", "rm", db.network, check=False)


@pytest.fixture(scope="session")
def web_image(daemon: None) -> Iterator[str]:
    """The frontend image, built once from the real Dockerfile in the real frontend/ context."""
    tag = f"stock-analyst-web-p6test:{RUN_ID}"
    docker("build", "--label", LABEL, "-t", tag, str(FRONTEND), timeout=1200)
    yield tag
    docker("rmi", "-f", tag, check=False)


@pytest.fixture(scope="module")
def stack(daemon: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    """The whole app profile (db, migrate, api, worker, web) running in an isolated Compose project."""
    running = Stack(tmp_path_factory.mktemp("stack"))
    # Fail fast, and for the right reason, before building or starting anything.
    expected = {"db", "migrate", "api", "worker", "web"}
    found = running.services()
    assert found == expected, (
        f"the `app` profile should define {sorted(expected)}, found {sorted(found)}"
    )
    try:
        result = running.up()
        assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
        yield running
    finally:
        running.down()
