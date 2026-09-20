"""The api container running with the flags Compose will use (read-only, no capabilities)."""

import json
import time

from helpers import (
    FAKE_ENV,
    FAKE_SECRET_VALUES,
    HARDENING,
    LABEL,
    docker,
    http_status,
    inspect_json,
    logs,
    output,
    running_api,
    wait_healthy,
)


def test_it_starts_and_the_image_healthcheck_reports_healthy(image: str) -> None:
    with running_api(image) as name:
        wait_healthy(name)
        assert http_status(name, "/api/healthz") == 200


def test_liveness_needs_no_database_but_readiness_does(image: str) -> None:
    """DATABASE_URL points nowhere: the container is alive, and honestly reports it is not ready."""
    with running_api(image) as name:
        wait_healthy(name)
        assert http_status(name, "/api/healthz") == 200
        assert http_status(name, "/api/readyz") == 503


def test_the_root_filesystem_is_really_read_only_and_only_tmp_is_writable(image: str) -> None:
    with running_api(image) as name:
        wait_healthy(name)
        write_app = docker("exec", name, "sh", "-c", "touch /app/nope", check=False)
        write_tmp = docker("exec", name, "sh", "-c", "touch /tmp/ok", check=False)
        assert write_app.returncode != 0
        assert write_tmp.returncode == 0


def test_logs_are_json_on_stdout_and_carry_no_secrets(image: str) -> None:
    with running_api(image) as name:
        wait_healthy(name)
        assert http_status(name, "/api/v1/me") == 401  # an INFO-level request log line
        time.sleep(1)
        text = logs(name)
        records = []
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # uvicorn's own start-up lines are plain text
        assert any(
            r.get("message") == "request_completed" and r.get("path") == "/api/v1/me"
            for r in records
        )
        for secret in FAKE_SECRET_VALUES:
            assert secret not in text


def test_stopping_it_is_graceful_and_quick(image: str) -> None:
    """Exec-form CMD means uvicorn is PID 1 and receives SIGTERM, so it drains and exits 0."""
    with running_api(image) as name:
        wait_healthy(name)
        started = time.monotonic()
        docker("stop", "-t", "15", name)
        elapsed = time.monotonic() - started
        assert elapsed < 8, f"took {elapsed:.1f}s: SIGTERM probably never reached uvicorn"
        assert inspect_json(name, "{{json .State.ExitCode}}") == 0


def test_a_missing_required_setting_stops_it_at_startup_naming_only_the_field(image: str) -> None:
    env = {k: v for k, v in FAKE_ENV.items() if k != "GOOGLE_CLIENT_SECRET"}
    args = ["run", "--rm", "--label", LABEL, *HARDENING]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    result = docker(*args, image, check=False, timeout=120)

    text = output(result)
    assert result.returncode != 0
    assert "google_client_secret" in text.lower()
    for secret in FAKE_SECRET_VALUES:
        assert secret not in text


def test_production_mode_refuses_insecure_settings(image: str) -> None:
    env = {**FAKE_ENV, "APP_ENV": "production", "COOKIE_SECURE": "false"}
    args = ["run", "--rm", "--label", LABEL, *HARDENING]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    result = docker(*args, image, check=False, timeout=120)

    text = output(result)
    assert result.returncode != 0
    assert "COOKIE_SECURE" in text
    for secret in FAKE_SECRET_VALUES:
        assert secret not in text
