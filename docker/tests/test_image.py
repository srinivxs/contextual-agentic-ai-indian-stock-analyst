"""The built backend image: who it runs as, what is inside it, how big it is, how it caches."""

import json
import shutil
import uuid
from pathlib import Path

import pytest

from helpers import (
    BACKEND,
    LABEL,
    docker,
    inspect_json,
    local_secret_values,
    output,
)

SIZE_FILE = Path(__file__).with_name("image-size-ceilings.json")

PRESENT = [
    "/app/src/app/main.py",
    "/app/migrations/env.py",
    "/app/alembic.ini",
    "/app/.venv/bin/uvicorn",
    "/app/.venv/bin/alembic",
]
ABSENT = [
    "/app/.env",
    "/app/.env.migration",
    "/app/tests",
    "/app/uv.lock",
    "/app/.git",
    "/app/.uv-cache",
    "/app/.venv/bin/pytest",  # dev dependencies stay out of the runtime image
    "/app/.venv/bin/ruff",
    "/app/.venv/bin/mypy",
    "/usr/local/bin/uv",  # the installer is a build tool, not runtime
    "/root/.cache",
]


def run_in_image(image: str, *command: str) -> str:
    return output(docker("run", "--rm", "--label", LABEL, image, *command))


def test_the_image_builds(image: str) -> None:
    docker("image", "inspect", image)


def test_it_runs_as_a_numeric_non_root_user(image: str) -> None:
    assert inspect_json(image, "{{json .Config.User}}") == "10001"
    assert run_in_image(image, "id", "-u").strip() == "10001"
    assert run_in_image(image, "id", "-g").strip() != "0"


def test_it_declares_what_it_needs_to_be_run(image: str) -> None:
    config = inspect_json(image, "{{json .Config}}")
    assert isinstance(config, dict)
    # Absent, null or empty depending on the image store; all mean "no entrypoint".
    assert not config.get("Entrypoint")
    assert config["Cmd"][0] == "uvicorn"
    assert "8000/tcp" in config["ExposedPorts"]
    assert "healthz" in " ".join(config["Healthcheck"]["Test"])
    assert config["WorkingDir"] == "/app"
    names = {entry.split("=", 1)[0] for entry in config["Env"]}
    assert not {n for n in names if any(k in n for k in ("SECRET", "PASSWORD", "TOKEN", "CLIENT"))}


def test_it_contains_what_it_must_and_nothing_it_should_not(image: str) -> None:
    probe = (
        "for p in {present}; do test -e $p || echo MISSING:$p; done; "
        "for p in {absent}; do test -e $p && echo UNEXPECTED:$p; done; echo checked"
    ).format(present=" ".join(PRESENT), absent=" ".join(ABSENT))
    result = run_in_image(image, "sh", "-c", probe)
    assert result.strip().splitlines() == ["checked"], result


def test_the_application_code_cannot_be_modified_by_the_user_running_it(image: str) -> None:
    result = run_in_image(
        image, "sh", "-c", "test ! -w /app/src/app/main.py && test ! -w /app/src && echo LOCKED"
    )
    assert result.strip() == "LOCKED"


def test_the_python_environment_works(image: str) -> None:
    assert "alembic" in run_in_image(image, "alembic", "--version").lower()
    assert "uvicorn" in run_in_image(image, "uvicorn", "--version").lower()
    imported = run_in_image(
        image, "python", "-c", "import sys; sys.path.insert(0, 'src'); import app.main; print('ok')"
    )
    assert imported.strip() == "ok"


def test_no_secret_is_recorded_in_the_image_history(image: str) -> None:
    history = output(docker("history", "--no-trunc", "--format", "{{.CreatedBy}}", image))
    leaked = [key for key, value in local_secret_values().items() if value in history]
    assert leaked == [], f"values of {leaked} appear in the image history"


def _unpacked_size_mb(image: str) -> float:
    """The sum of the layers' unpacked sizes.

    Not `docker image inspect .Size`: with Docker's containerd image store that is the compressed
    size, while the classic store reports the unpacked size, so the same image would "measure"
    differently on a laptop and on a CI runner. The layer sizes are the same everywhere.
    """
    history = docker("history", "--human=false", "--format", "{{.Size}}", image).stdout
    return sum(int(line) for line in history.split()) / 1_000_000


def test_the_image_stays_within_its_documented_size_ceiling(image: str) -> None:
    """The ceiling is set from the first measured clean build, with a written justification.

    Its purpose is to catch accidental bloat (a forgotten cache, a dev dependency, a copied
    directory), not to chase a number. The ceiling may not drift more than 25% above the baseline
    without re-measuring and re-justifying it.
    """
    assert SIZE_FILE.is_file(), "no baseline yet: measure the first clean build and record it"
    entry = json.loads(SIZE_FILE.read_text(encoding="utf-8"))["backend"]
    assert entry["justification"].strip(), "a ceiling needs a written justification"
    assert entry["ceiling_mb"] <= entry["baseline_mb"] * 1.25, (
        "the ceiling drifted from the baseline"
    )
    actual = _unpacked_size_mb(image)
    assert actual <= entry["ceiling_mb"], (
        f"image is {actual:.0f} MB, over the {entry['ceiling_mb']} MB ceiling"
    )


def test_a_source_change_reuses_the_dependency_layers(daemon: None, tmp_path: Path) -> None:
    """Dependencies are installed before the source is copied, so editing code is a fast rebuild."""
    context = tmp_path / "backend"
    shutil.copytree(
        BACKEND,
        context,
        ignore=shutil.ignore_patterns(
            ".venv",
            ".uv-cache",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            ".coverage",
            "__pycache__",
            "tests",
        ),
    )
    first, second = (f"stock-analyst-api-p6cache:{uuid.uuid4().hex[:8]}" for _ in range(2))
    try:
        docker("build", "--label", LABEL, "-t", first, str(context))
        with (context / "src" / "app" / "__init__.py").open("a", encoding="utf-8") as handle:
            handle.write("\n# a source-only change\n")
        docker("build", "--label", LABEL, "-t", second, str(context))

        layers_a = inspect_json(first, "{{json .RootFS.Layers}}")
        layers_b = inspect_json(second, "{{json .RootFS.Layers}}")
        assert isinstance(layers_a, list)
        assert isinstance(layers_b, list)
        assert len(layers_a) == len(layers_b)
        assert layers_a != layers_b
        shared = 0
        for a, b in zip(layers_a, layers_b, strict=True):
            if a != b:
                break
            shared += 1
        rebuilt = len(layers_a) - shared
        assert 1 <= rebuilt <= 3, f"{rebuilt} layers changed; dependencies should have been cached"
    finally:
        docker("rmi", "-f", first, second, check=False)


@pytest.mark.parametrize("path", ["/app/src/app/main.py", "/app/alembic.ini"])
def test_application_files_are_owned_by_root_not_the_runtime_user(image: str, path: str) -> None:
    owner = run_in_image(image, "stat", "-c", "%u", path).strip()
    assert owner == "0"
