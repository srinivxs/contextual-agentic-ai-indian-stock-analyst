"""What the backend Dockerfile and .dockerignore must say. Read as text: no Docker daemon needed.

Each test fails (never passes vacuously) when the file does not exist yet.
"""

import json
import re

import pytest

from helpers import BACKEND, Instruction, final_stage, parse_dockerfile

DOCKERFILE = BACKEND / "Dockerfile"
DOCKERIGNORE = BACKEND / ".dockerignore"


@pytest.fixture(scope="module")
def instructions() -> list[Instruction]:
    assert DOCKERFILE.is_file(), "backend/Dockerfile does not exist"
    return parse_dockerfile(DOCKERFILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def last(instructions: list[Instruction]) -> list[Instruction]:
    return final_stage(instructions)


def test_the_dockerfile_uses_unix_line_endings() -> None:
    assert DOCKERFILE.is_file(), "backend/Dockerfile does not exist"
    assert b"\r" not in DOCKERFILE.read_bytes(), (
        "CRLF breaks Linux builds (.gitattributes forces LF)"
    )


def test_it_is_a_multi_stage_build(instructions: list[Instruction]) -> None:
    froms = [i for i in instructions if i.name == "FROM"]
    assert len(froms) >= 2, "a builder stage plus a minimal runtime stage"


def test_every_base_image_is_pinned_to_a_patch_level_tag(instructions: list[Instruction]) -> None:
    """No `latest`, no floating minor tag. Digest pinning is deliberately deferred to P8."""
    external = []
    stage_names: set[str] = set()
    for i in instructions:
        if i.name != "FROM":
            continue
        words = [w for w in i.args.split() if not w.startswith("--")]
        reference = words[0]
        if reference not in stage_names:  # `FROM builder` refers to an earlier stage
            external.append(reference)
        if "as" in [w.lower() for w in words]:
            stage_names.add(words[-1])
    assert external, "no external base image found"
    for reference in external:
        assert ":" in reference, f"{reference} has no tag (that means latest)"
        tag = reference.split(":", 1)[1]
        assert tag != "latest", reference
        assert re.search(r"\d+\.\d+\.\d+", tag), f"{reference} is not pinned to a patch version"


def test_the_runtime_stage_runs_as_a_numeric_non_root_user(last: list[Instruction]) -> None:
    users = [i.args.split(":")[0] for i in last if i.name == "USER"]
    assert users, "the final stage never switches away from root"
    assert users[-1].isdigit(), f"USER {users[-1]} is not a numeric uid"
    assert int(users[-1]) >= 1000, f"USER {users[-1]} is a system uid"


def test_the_default_command_is_the_api_in_exec_form(last: list[Instruction]) -> None:
    commands = [i for i in last if i.name == "CMD"]
    assert len(commands) == 1
    args = commands[0].args
    assert args.startswith("["), "shell form would make /bin/sh PID 1 and swallow SIGTERM"
    command = json.loads(args)
    assert command[:3] == ["uvicorn", "app.main:create_app", "--factory"] or "uvicorn" in command[0]
    joined = " ".join(command)
    for required in (
        "app.main:create_app",
        "--factory",
        "--app-dir src",
        "--host 0.0.0.0",
        "--port 8000",
    ):
        assert required in joined, f"CMD is missing {required!r}"
    # ADR 012: the backend never trusts X-Forwarded-*, and a container has no business reloading.
    for forbidden in ("--proxy-headers", "--forwarded-allow-ips", "--reload"):
        assert forbidden not in joined, f"CMD must not use {forbidden}"


def test_there_is_no_entrypoint_so_command_can_be_overridden_cleanly(
    instructions: list[Instruction],
) -> None:
    """`migrate` (and later `worker`) are the same image with a different `command:`."""
    assert not [i for i in instructions if i.name == "ENTRYPOINT"]


def test_the_image_carries_its_own_liveness_healthcheck(last: list[Instruction]) -> None:
    checks = [i for i in last if i.name == "HEALTHCHECK"]
    assert len(checks) == 1
    text = checks[0].args
    assert "/api/healthz" in text, "liveness, not /api/readyz: a database blip must not fail it"
    assert "python" in text
    assert "curl" not in text
    assert "wget" not in text
    interval = re.search(r"--interval=(\d+)s", text)
    assert interval is not None
    assert int(interval.group(1)) <= 30


def test_no_secret_can_be_baked_in_through_env_or_build_arguments(
    instructions: list[Instruction],
) -> None:
    risky = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|CLIENT|DATABASE_URL", re.IGNORECASE)
    for i in instructions:
        if i.name in {"ENV", "ARG"}:
            names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", i.args) or [i.args.split()[0]]
            for name in names:
                assert not risky.search(name), f"{i.name} {name} could bake a secret into a layer"


def test_env_files_are_never_copied_and_the_whole_context_is_never_copied(
    instructions: list[Instruction],
) -> None:
    for i in instructions:
        if i.name in {"COPY", "ADD"}:
            sources = [w for w in i.args.split() if not w.startswith("--")][:-1]
            for source in sources:
                assert ".env" not in source, f"{i.name} {source}"
                assert source not in {".", "./", "*"}, f"{i.name} copies the whole context"


def test_the_runtime_stage_installs_nothing(last: list[Instruction]) -> None:
    """Dependencies come from the builder; the runtime image stays minimal and fixed."""
    for i in last:
        if i.name == "RUN":
            for forbidden in ("pip install", "uv ", "apt-get", "apk add", "curl "):
                assert forbidden not in i.args, f"runtime RUN uses {forbidden!r}"


def test_dockerignore_keeps_secrets_caches_and_tests_out_of_the_build_context() -> None:
    assert DOCKERIGNORE.is_file(), "backend/.dockerignore does not exist"
    entries = {
        line.strip().rstrip("/")
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    required = {
        ".env",
        ".env.*",
        ".venv",
        ".uv-cache",
        ".git",
        "**/__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".coverage",
        "tests",
    }
    assert required <= entries, f"missing from .dockerignore: {sorted(required - entries)}"


def test_the_blob_folder_is_the_one_path_the_app_user_owns(last: list[Instruction]) -> None:
    """A named volume mounted at /data/blobs takes this folder's owner, so the api and the worker
    can write stored PDFs there while the rest of the filesystem stays read-only (P9d)."""
    runs = " ".join(i.args for i in last if i.name == "RUN")
    assert "mkdir -p /data/blobs" in runs
    assert "chown 10001:10001 /data/blobs" in runs

