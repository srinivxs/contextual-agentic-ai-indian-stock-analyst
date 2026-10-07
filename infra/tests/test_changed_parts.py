"""Tests for .github/scripts/changed_parts.py: which parts of the repository a push changed.

A push that changes only frontend/ skips the backend tests, the image and the backend rollout and
publishes the site at once (the owner, 2026-10-08: a page change took 12 minutes to go live). Any
other change runs everything, and so does any doubt.

Run from the repository root:
    backend\\.venv\\Scripts\\python -m pytest infra/tests
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".github" / "scripts" / "changed_parts.py"
EVERYTHING = {"backend": True, "frontend": True}


def _load() -> ModuleType:
    if not SCRIPT.is_file():
        pytest.fail(f"{SCRIPT.relative_to(REPO)} does not exist")
    spec = importlib.util.spec_from_file_location("changed_parts", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["changed_parts"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def changed_parts() -> ModuleType:
    return _load()


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, path: str) -> str:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"{path} {len(list(repo.rglob('*')))}\n", encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"change {path}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "backend/app.py")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- which parts --------------------------------------------------------------------------------


def test_a_frontend_only_change_skips_the_backend(changed_parts: ModuleType) -> None:
    paths = ["frontend/src/app/globals.css", "frontend/tests/lib/motion.test.ts"]
    assert changed_parts.parts(paths) == {"backend": False, "frontend": True}


def test_a_backend_only_change_does_not_republish_the_site(changed_parts: ModuleType) -> None:
    assert changed_parts.parts(["backend/src/app/worker.py"]) == {
        "backend": True,
        "frontend": False,
    }


def test_both_parts_when_both_changed(changed_parts: ModuleType) -> None:
    assert changed_parts.parts(["frontend/src/a.tsx", "backend/src/b.py"]) == EVERYTHING


def test_anything_outside_frontend_runs_the_backend_path(changed_parts: ModuleType) -> None:
    # The workflow, Terraform, Compose and the docs all take the full path: simple, and safe.
    for path in [".github/workflows/pipeline.yml", "infra/stack/compute.tf", "README.md"]:
        assert changed_parts.parts([path])["backend"] is True, path


def test_a_name_that_only_starts_like_frontend_is_not_frontend(changed_parts: ModuleType) -> None:
    assert changed_parts.parts(["frontend-old/x.ts"]) == {"backend": True, "frontend": False}


def test_nothing_to_compare_means_everything(changed_parts: ModuleType) -> None:
    # No green run yet, a base this clone does not know, or a re-run of the same commit.
    assert changed_parts.parts([]) == EVERYTHING


# --- what changed since the last green run ------------------------------------------------------


def test_lists_every_file_changed_since_the_base(changed_parts: ModuleType, repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "frontend/src/a.tsx")
    _commit(repo, "frontend/src/b.css")
    assert sorted(changed_parts.changed_since(base)) == ["frontend/src/a.tsx", "frontend/src/b.css"]


def test_a_failed_backend_push_is_still_counted_by_the_next_frontend_push(
    changed_parts: ModuleType, repo: Path
) -> None:
    # The base is the last GREEN run, not the previous push: a backend change whose run failed is
    # still undeployed, so the next push, even a page fix, runs the backend path again.
    green = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "backend/src/broken.py")  # this push's run failed
    _commit(repo, "frontend/src/fix.tsx")
    assert changed_parts.parts(changed_parts.changed_since(green)) == EVERYTHING


@pytest.mark.parametrize("base", ["", "HEAD~1", "--output=/tmp/x", "abc123", "A" * 40])
def test_anything_but_a_full_commit_id_is_not_passed_to_git(
    changed_parts: ModuleType, repo: Path, base: str
) -> None:
    _commit(repo, "frontend/src/a.tsx")
    assert changed_parts.changed_since(base) == []


def test_a_base_this_clone_does_not_know_means_everything(
    changed_parts: ModuleType, repo: Path
) -> None:
    assert changed_parts.changed_since("0" * 40) == []
    assert changed_parts.changed_since("f" * 40) == []


# --- what the pipeline reads -------------------------------------------------------------------


def test_main_writes_both_answers_for_the_workflow(
    changed_parts: ModuleType, repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "frontend/src/a.tsx")
    output = tmp_path_factory.mktemp("out") / "github_output"
    output.write_text("earlier=1\n", encoding="utf-8")

    assert changed_parts.main(["changed_parts.py", base], {"GITHUB_OUTPUT": str(output)}) == 0
    assert output.read_text(encoding="utf-8") == "earlier=1\nbackend=false\nfrontend=true\n"


def test_main_without_a_base_runs_everything(
    changed_parts: ModuleType, repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    output = tmp_path_factory.mktemp("out") / "github_output"
    assert changed_parts.main(["changed_parts.py", ""], {"GITHUB_OUTPUT": str(output)}) == 0
    assert output.read_text(encoding="utf-8") == "backend=true\nfrontend=true\n"
