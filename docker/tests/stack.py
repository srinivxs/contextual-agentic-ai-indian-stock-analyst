"""A throwaway Compose stack for the P6b tests.

The real docker-compose.yml is used, but under a random project name (so containers, network and
volumes are separate from the development stack), random free loopback ports, and fake credentials.
Two things keep the developer's real configuration out:

* `--env-file` points at a generated file, so Compose never reads the real `.env`;
* every variable the compose file uses is removed from the inherited environment first (a real
  process environment variable would otherwise beat `--env-file`).

If a run is killed half way, clean up with:
    docker rm -f -v $(docker ps -aq --filter name=p6btest-)
    docker network rm $(docker network ls -q --filter name=p6btest-)
    docker volume rm $(docker volume ls -q --filter name=p6btest-)
"""

import hashlib
import json
import os
import secrets
import socket
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from helpers import REPO, RUN_ID, wait_for

COMPOSE_FILE = REPO / "docker-compose.yml"

# Every variable name the compose file (or the app behind it) can read. Removed from the inherited
# environment so a developer's exported value can never leak into a test stack.
COMPOSE_VARIABLES = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "APP_DB_USER",
    "APP_DB_PASSWORD",
    "DB_PORT",
    "WEB_PORT",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "SESSION_SECRET",
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "PUBLIC_BASE_URL",
    "APP_ENV",
    "COOKIE_SECURE",
    "LOG_LEVEL",
    "FILINGS_DISCOVERY",
    "EMBEDDINGS_ENABLED",
    "EXTRACTION_ENABLED",
    # A developer's temporary AWS pass (P10) must never reach a test stack, let alone Bedrock.
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Stack:
    """One isolated Compose project. Cheap to create; nothing runs until `up` is called."""

    def __init__(
        self,
        directory: Path,
        *,
        drop: Sequence[str] = (),
        extra: dict[str, str] | None = None,
        override_files: Sequence[Path] = (),
    ) -> None:
        self.project = f"p6btest-{RUN_ID}-{secrets.token_hex(2)}"
        self.web_port = free_port()
        self.db_port = free_port()
        self.admin_user = "p6b_admin"
        self.admin_password = secrets.token_hex(12)
        self.app_user = "p6b_app"
        self.app_password = secrets.token_hex(12)
        self.db_name = "p6b_stock"
        self.google_secret = "GOCSPX-p6b-fake-client-secret-never-real"  # noqa: S105
        self.session_secret = "p6b-fake-session-secret-at-least-32-characters"  # noqa: S105
        self.env: dict[str, str] = {
            "POSTGRES_USER": self.admin_user,
            "POSTGRES_PASSWORD": self.admin_password,
            "POSTGRES_DB": self.db_name,
            "APP_DB_USER": self.app_user,
            "APP_DB_PASSWORD": self.app_password,
            "DB_PORT": str(self.db_port),
            "WEB_PORT": str(self.web_port),
            "GOOGLE_CLIENT_ID": "1234567890-p6bfake.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": self.google_secret,
            "SESSION_SECRET": self.session_secret,
            **(extra or {}),
        }
        for name in drop:
            self.env.pop(name, None)
        self.env_file = directory / f"{self.project}.env"
        self.env_file.write_text(
            "".join(f"{k}={v}\n" for k, v in self.env.items()), encoding="utf-8", newline="\n"
        )
        self.override_files = list(override_files)

    # --- running compose -----------------------------------------------------------------

    def compose(
        self,
        *args: str,
        profile: bool = True,
        check: bool = True,
        timeout: int = 900,
    ) -> subprocess.CompletedProcess[str]:
        command = ["docker", "compose", "-p", self.project, "--env-file", str(self.env_file)]
        for path in (COMPOSE_FILE, *self.override_files):
            command += ["-f", str(path)]
        if profile:
            command += ["--profile", "app"]
        command += list(args)
        environment = {k: v for k, v in os.environ.items() if k not in COMPOSE_VARIABLES}
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=environment,
        )
        if check and proc.returncode != 0:
            tail = (proc.stdout + proc.stderr)[-3000:]
            raise AssertionError(f"`docker compose {' '.join(args[:2])} ...` failed:\n{tail}")
        return proc

    def config(self, *, profile: bool = True) -> dict[str, Any]:
        """The resolved compose model (variables substituted) as a dict."""
        result = self.compose("config", "--format", "json", profile=profile)
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def services(self, *, profile: bool = True) -> set[str]:
        result = self.compose("config", "--services", profile=profile)
        return set(result.stdout.split())

    def up(
        self, *services: str, profile: bool = True, timeout: int = 900
    ) -> subprocess.CompletedProcess[str]:
        return self.compose(
            "up",
            "-d",
            "--build",
            "--wait",
            *services,
            profile=profile,
            check=False,
            timeout=timeout,
        )

    def down(self) -> None:
        self.compose("down", "-v", "--remove-orphans", "--rmi", "local", check=False)

    # --- looking at what is running ----------------------------------------------------

    def container(self, service: str) -> str | None:
        """The container id of a service, or None when no container exists for it."""
        ids = self.compose("ps", "-aq", service, check=False).stdout.split()
        return ids[0] if ids else None

    def never_started(self, service: str) -> bool:
        """True if the service has no container, or one that was created but never ran.

        `docker compose up` creates the containers of dependent services up front and starts each
        one only when what it depends on is ready, so "blocked" means `created` with a zero start
        time, not "absent".
        """
        if self.container(service) is None:
            return True
        status = self.inspect(service, "{{json .State.Status}}")
        started = self.inspect(service, "{{json .State.StartedAt}}")
        return bool(status == "created" and str(started).startswith("0001-01-01"))

    def inspect(self, service: str, template: str) -> Any:
        container = self.container(service)
        assert container, f"no container exists for service {service!r}"
        result = subprocess.run(
            ["docker", "inspect", "-f", template, container],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=60,
        )
        return json.loads(result.stdout)

    def wait_healthy(self, service: str, timeout: float = 120.0) -> None:
        def ready() -> bool:
            return bool(
                self.inspect(service, "{{json .State.Health.Status}}") == "healthy"
                and self.inspect(service, "{{json .State.Status}}") == "running"
            )

        wait_for(ready, f"{service} to become healthy", timeout)

    def psql(self, sql: str) -> str:
        result = self.compose(
            "exec", "-T", "db", "psql", "-U", self.admin_user, "-d", self.db_name, "-Atc", sql
        )
        return result.stdout.strip()

    # --- talking to it as a browser would ---------------------------------------------

    @property
    def origin(self) -> str:
        """What a browser on the host puts in the Origin header for this stack."""
        return f"http://localhost:{self.web_port}"

    def client(self, cookie: str | None = None) -> httpx.Client:
        headers = {"Cookie": f"session={cookie}"} if cookie else {}
        return httpx.Client(
            base_url=f"http://127.0.0.1:{self.web_port}",
            follow_redirects=False,
            timeout=10.0,
            headers=headers,
        )


def seed_session(query: Any, email: str = "p6b@example.test") -> str:
    """Insert a user and a one-hour session straight into the database; return the raw cookie.

    The database holds only the token's SHA-256 (ADR 012), so a test can log in without Google by
    writing that row itself. `query` runs one SQL statement as the admin role and returns stdout.
    """
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    # Test-only SQL built from values this function generates itself (never from outside input).
    query(
        "with u as (insert into users (google_sub, email) "  # noqa: S608
        f"values ('p6b-sub-{secrets.token_hex(4)}', '{email}') returning id) "
        "insert into sessions (user_id, token_hash, expires_at) "
        f"select id, decode('{digest}', 'hex'), now() + interval '1 hour' from u"
    )
    return token
