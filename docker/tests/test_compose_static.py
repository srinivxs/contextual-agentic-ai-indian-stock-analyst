"""What docker-compose.yml must say, checked on the resolved model (`docker compose config`).

Nothing is started. Each test fails (never passes vacuously) while the `app` profile is missing.
The wiring that matters is the security shape: who gets admin credentials, what is published to the
host, and what the api container is not allowed to do.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from helpers import BACKEND, FRONTEND, local_secret_values
from stack import COMPOSE_FILE, Stack

APP_SERVICES = {"db", "migrate", "api", "worker", "web"}


@pytest.fixture(scope="module")
def stack(daemon: None, tmp_path_factory: pytest.TempPathFactory) -> Stack:
    # Shadows the running-stack fixture in conftest: this module never starts containers.
    return Stack(tmp_path_factory.mktemp("compose-static"))


@pytest.fixture(scope="module")
def model(stack: Stack) -> dict[str, Any]:
    services = stack.services()
    assert services == APP_SERVICES, f"the `app` profile defines {sorted(services)}"
    return stack.config()


def service(model: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = model["services"][name]
    return found


# --- the default stays database-only -----------------------------------------------------


def test_without_the_profile_only_the_database_is_defined(stack: Stack) -> None:
    assert stack.services(profile=True) == APP_SERVICES, "the `app` profile is missing"
    assert stack.services(profile=False) == {"db"}


def test_database_only_use_needs_only_the_database_passwords(daemon: None, tmp_path: Path) -> None:
    """Compose enforces `${VAR:?}` even for services in an inactive profile, so the app-only
    secrets must not use it: `docker compose up -d --wait` has to keep working with a `.env` that
    has no Google settings at all."""
    bare = Stack(
        tmp_path,
        drop=["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "WEB_PORT"],
    )
    assert bare.services(profile=False) == {"db"}
    assert bare.services(profile=True) == APP_SERVICES, "the `app` profile is missing"


@pytest.mark.parametrize("missing", ["POSTGRES_PASSWORD", "APP_DB_PASSWORD"])
def test_a_missing_database_password_stops_compose_and_names_only_the_variable(
    daemon: None, tmp_path: Path, missing: str
) -> None:
    broken = Stack(tmp_path, drop=[missing])
    result = broken.compose("config", "--services", profile=False, check=False)
    assert result.returncode != 0
    text = result.stdout + result.stderr
    assert missing in text
    for value in broken.env.values():
        assert value not in text, "a value was echoed into the error"


# --- what is published to the host --------------------------------------------------------


def test_only_the_database_and_web_publish_ports_and_only_on_loopback(
    stack: Stack, model: dict[str, Any]
) -> None:
    assert not service(model, "api").get("ports"), "the api must not be reachable from the host"
    assert not service(model, "migrate").get("ports")
    for name in APP_SERVICES:
        for port in service(model, name).get("ports", []):
            assert port["host_ip"] == "127.0.0.1", f"{name} publishes {port} beyond loopback"
    (web_port,) = service(model, "web")["ports"]
    assert web_port["target"] == 8080
    assert int(web_port["published"]) == stack.web_port, "the host port must come from WEB_PORT"


def test_no_service_pins_a_container_name(model: dict[str, Any]) -> None:
    """A fixed name would make two projects (the dev stack and a test stack) collide."""
    for name in APP_SERVICES:
        assert "container_name" not in service(model, name), name


# --- who gets which credentials -----------------------------------------------------------


def test_no_service_uses_env_file_so_nothing_reaches_a_container_by_accident() -> None:
    lines = [
        line.split("#", 1)[0] for line in COMPOSE_FILE.read_text(encoding="utf-8").splitlines()
    ]
    assert not [line for line in lines if re.search(r"\benv_file\b", line)]
    assert "services:" in "\n".join(lines)


def test_only_migrate_holds_the_admin_credentials(stack: Stack, model: dict[str, Any]) -> None:
    for name in ("api", "worker", "web"):
        dump = repr(service(model, name))
        assert stack.admin_password not in dump, f"{name} was given the admin password"
        assert stack.admin_user not in dump, f"{name} was given the admin role"
        assert "MIGRATION_DATABASE_URL" not in dump, name

    migrate_env = service(model, "migrate")["environment"]
    url = migrate_env["MIGRATION_DATABASE_URL"]
    assert url.startswith(
        f"postgresql+asyncpg://{stack.admin_user}:{stack.admin_password}@db:5432/"
    )
    dump = repr(service(model, "migrate"))
    assert stack.app_password not in dump, "migrate does not need the runtime role"
    assert stack.google_secret not in dump, "migrate does not need the Google secret"
    assert stack.session_secret not in dump, "migrate does not need the session secret"


def test_the_api_gets_the_runtime_role_and_exactly_the_settings_it_needs(
    stack: Stack, model: dict[str, Any]
) -> None:
    env = service(model, "api")["environment"]
    assert env["DATABASE_URL"] == (
        f"postgresql+asyncpg://{stack.app_user}:{stack.app_password}@db:5432/{stack.db_name}"
    )
    assert env["PUBLIC_BASE_URL"] == f"http://localhost:{stack.web_port}", (
        "the origin the browser sees is the published web port (this drives the Origin check)"
    )
    assert env["GOOGLE_CLIENT_ID"] == stack.env["GOOGLE_CLIENT_ID"]
    assert env["GOOGLE_CLIENT_SECRET"] == stack.google_secret
    assert env["SESSION_SECRET"] == stack.session_secret
    allowed = {
        "DATABASE_URL",
        "PUBLIC_BASE_URL",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "SESSION_SECRET",
        "APP_ENV",
        "COOKIE_SECURE",
        "LOG_LEVEL",
    }
    assert set(env) <= allowed, f"unexpected api environment: {sorted(set(env) - allowed)}"


def test_the_worker_gets_the_runtime_role_and_no_login_secrets(
    stack: Stack, model: dict[str, Any]
) -> None:
    """Least privilege (P9b): the worker handles no logins, so it never sees the Google client
    secret or the session secret. Its settings are CommonSettings, which does not even have them."""
    worker = service(model, "worker")
    env = worker["environment"]
    assert env["DATABASE_URL"] == (
        f"postgresql+asyncpg://{stack.app_user}:{stack.app_password}@db:5432/{stack.db_name}"
    )
    dump = repr(worker)
    assert stack.google_secret not in dump
    assert stack.session_secret not in dump
    allowed = {
        "DATABASE_URL",
        "BLOB_ROOT",
        "FILINGS_DISCOVERY",
        "APP_ENV",
        "LOG_LEVEL",
        "PYTHONPATH",
    }
    assert set(env) <= allowed, f"unexpected worker environment: {sorted(set(env) - allowed)}"
    assert worker["command"] == ["python", "-m", "app.worker"]
    # `python -m app.worker` finds the package only with this; without it the worker exits at once.
    assert env["PYTHONPATH"] == "/app/src"
    assert "ports" not in worker


def test_filing_discovery_is_off_unless_the_developer_switches_it_on(model: dict[str, Any]) -> None:
    """ADR 018: a plain `docker compose --profile app up` never reaches screener.in or BSE."""
    assert service(model, "worker")["environment"]["FILINGS_DISCOVERY"] == "false"


def test_only_the_worker_has_the_folder_for_stored_files(model: dict[str, Any]) -> None:
    """The worker fetches filings and reads them back, from a named volume. The api stores nothing
    (no uploads since P9d), so it gets no volume at all."""
    worker = service(model, "worker")
    assert worker["environment"]["BLOB_ROOT"] == "/data/blobs"
    mounts = [
        (m["type"], m["source"], m["target"], m.get("read_only", False))
        for m in worker.get("volumes", [])
    ]
    assert ("volume", "blobs", "/data/blobs", False) in mounts, mounts
    assert "blobs" in model["volumes"]
    assert "volumes" not in service(model, "api")


def test_the_worker_has_its_own_health_check_not_the_apis(model: dict[str, Any]) -> None:
    """The image's HEALTHCHECK calls the api's /api/healthz, which a worker does not serve: left in
    place, the worker would be reported unhealthy and `up --wait` would fail."""
    health = service(model, "worker").get("healthcheck", {})
    assert health.get("disable") is True or "healthz" not in repr(health.get("test"))


def test_the_web_container_receives_no_configuration_or_secrets(
    stack: Stack, model: dict[str, Any]
) -> None:
    dump = repr(service(model, "web"))
    for secret in (
        stack.google_secret,
        stack.session_secret,
        stack.app_password,
        stack.admin_password,
    ):
        assert secret not in dump
    assert not service(model, "web").get("environment")


def test_the_real_local_secrets_can_never_reach_a_test_stack(
    stack: Stack, model: dict[str, Any]
) -> None:
    """The stack is built from generated fake values only, never from the developer's `.env`."""
    rendered = repr(model)
    leaked = [key for key, value in local_secret_values().items() if value in rendered]
    assert leaked == [], f"values of {leaked} reached the test stack"


# --- start-up order and what the api may do -----------------------------------------------


def test_startup_order_is_db_then_migrate_then_api_then_web(model: dict[str, Any]) -> None:
    migrate = service(model, "migrate")
    assert migrate["depends_on"]["db"]["condition"] == "service_healthy"
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert migrate.get("restart", "no") == "no", "a one-off task must not be restarted"

    api = service(model, "api")
    assert api["depends_on"]["migrate"]["condition"] == "service_completed_successfully"

    worker = service(model, "worker")
    assert worker["depends_on"]["migrate"]["condition"] == "service_completed_successfully"

    web = service(model, "web")
    assert web["depends_on"]["api"]["condition"] == "service_healthy"


def test_api_and_migrate_are_built_from_the_same_backend_image_definition(
    model: dict[str, Any],
) -> None:
    """One image, several commands (ADR 008). Neither service names an image tag: Compose then
    tags what it builds with the project name, so a test stack (or a second checkout) can never
    overwrite a tag the developer's own stack uses."""
    api, migrate, web = (service(model, name) for name in ("api", "migrate", "web"))
    assert api["build"] == migrate["build"], "api and migrate must build the identical image"
    assert service(model, "worker")["build"] == api["build"], "the worker is the same image too"
    assert Path(api["build"]["context"]).resolve() == BACKEND.resolve()
    assert Path(web["build"]["context"]).resolve() == FRONTEND.resolve()
    for name in ("api", "migrate", "worker", "web"):
        assert "image" not in service(model, name), f"{name} names an image tag"


@pytest.mark.parametrize("name", ["api", "worker", "web"])
def test_the_long_running_containers_have_a_read_only_filesystem_and_no_capabilities(
    model: dict[str, Any], name: str
) -> None:
    container = service(model, name)
    assert container.get("read_only") is True
    assert any(str(t).split(":")[0] == "/tmp" for t in container.get("tmpfs", [])), container.get(
        "tmpfs"
    )
    assert container.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in container.get("security_opt", [])
    assert "cap_add" not in container
    assert container.get("privileged") in (None, False)
