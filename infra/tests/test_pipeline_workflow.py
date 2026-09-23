"""Guards for the GitHub Actions pipeline (P8b).

The workflow is the one piece of this repository that holds AWS power, so the rules that keep that
power small are checked here as configuration: which events start it, which job may ask for an AWS
token, what the pushed image is called, and that every third-party action is pinned to a commit.

These tests read the YAML. They cannot prove GitHub accepts it or that AWS trusts the token; only a
real run on main proves that.

Run from the repository root:
    backend\\.venv\\Scripts\\python -m pytest infra/tests
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "pipeline.yml"
CHECK_JOBS = {"backend", "frontend", "infra"}
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    if not WORKFLOW.is_file():
        pytest.fail(f"{WORKFLOW.relative_to(REPO)} does not exist")
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _jobs(workflow: dict[Any, Any]) -> dict[str, dict[str, Any]]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert jobs, "the workflow has no jobs"
    return jobs


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    assert steps, "a job has no steps"
    return steps


def _scripts(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def test_it_runs_on_every_push_to_main_and_nothing_else(workflow: dict[Any, Any]) -> None:
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1).
    triggers = workflow.get(True, workflow.get("on"))
    assert isinstance(triggers, dict)
    assert set(triggers) == {"push", "workflow_dispatch"}, (
        "Only a push to main (and a manual re-run) may start it. pull_request_target in particular "
        "runs a fork's code with this repository's permissions."
    )
    assert triggers["push"] == {"branches": ["main"]}


def test_the_default_token_can_only_read(workflow: dict[Any, Any]) -> None:
    assert workflow.get("permissions") == {"contents": "read"}


def test_only_the_image_job_can_ask_for_an_aws_token(workflow: dict[Any, Any]) -> None:
    asking = {
        name
        for name, job in _jobs(workflow).items()
        if (job.get("permissions") or {}).get("id-token") == "write"
    }
    assert asking == {"image"}, (
        "An OIDC token is what AWS trades for credentials. The jobs that run tests never need one."
    )


def test_nothing_is_pushed_unless_every_check_passed(workflow: dict[Any, Any]) -> None:
    jobs = _jobs(workflow)
    assert set(jobs) >= CHECK_JOBS, f"missing check jobs: {CHECK_JOBS - set(jobs)}"
    needs = jobs["image"].get("needs")
    assert isinstance(needs, list)
    assert set(needs) == CHECK_JOBS


def test_every_action_is_pinned_to_a_commit(workflow: dict[Any, Any]) -> None:
    # A tag like v4 can be moved by whoever controls the action's repository; a commit SHA cannot.
    used = [
        step["uses"] for job in _jobs(workflow).values() for step in _steps(job) if "uses" in step
    ]
    assert len(used) >= 5, "expected checkout, setup and AWS actions"
    unpinned = [action for action in used if not PINNED_ACTION.match(action)]
    assert not unpinned, f"pin these to a 40-character commit SHA: {unpinned}"


def test_aws_is_reached_through_oidc_and_never_a_stored_key(workflow: dict[Any, Any]) -> None:
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    for forbidden in ("aws-access-key-id", "aws-secret-access-key", "aws_secret_access_key"):
        assert forbidden not in text

    login = [
        step
        for step in _steps(_jobs(workflow)["image"])
        if str(step.get("uses", "")).startswith("aws-actions/configure-aws-credentials@")
    ]
    assert len(login) == 1
    assert login[0]["with"] == {
        "role-to-assume": "${{ vars.CI_ROLE_ARN }}",
        "aws-region": "ap-south-1",
    }


def test_no_job_declares_an_environment(workflow: dict[Any, Any]) -> None:
    # With `environment:` GitHub puts `environment:<name>` in the token's subject instead of the
    # branch, and the role's trust policy (infra/cicd/github.tf) would refuse it.
    assert not [name for name, job in _jobs(workflow).items() if "environment" in job]


def test_the_image_is_named_after_its_commit(workflow: dict[Any, Any]) -> None:
    scripts = _scripts(_jobs(workflow)["image"])
    assert "GITHUB_SHA" in scripts
    assert "latest" not in scripts, "tags are immutable commit SHAs; there is no latest"


def test_a_rerun_of_the_same_commit_does_not_fail_on_the_immutable_tag(
    workflow: dict[Any, Any],
) -> None:
    # ECR refuses a second push to an existing tag, so the job must look before it pushes.
    scripts = _scripts(_jobs(workflow)["image"])
    assert "batch-get-image" in scripts
    assert scripts.index("batch-get-image") < scripts.index("docker push")


def test_two_runs_never_overlap_and_a_run_is_never_cut_short(workflow: dict[Any, Any]) -> None:
    concurrency = workflow.get("concurrency")
    assert isinstance(concurrency, dict)
    assert concurrency.get("cancel-in-progress") is False


def test_the_integration_tests_run_against_a_real_database(workflow: dict[Any, Any]) -> None:
    scripts = _scripts(_jobs(workflow)["backend"])
    assert "docker compose up -d --wait" in scripts
    assert "pytest --cov" in scripts
    assert "not integration" not in scripts, "integration tests fail, never skip"


def test_database_passwords_are_generated_per_run_not_written_down(
    workflow: dict[Any, Any],
) -> None:
    for name, job in _jobs(workflow).items():
        env = job.get("env") or {}
        assert "POSTGRES_PASSWORD" not in env, name
        assert "APP_DB_PASSWORD" not in env, name
    assert "openssl rand" in _scripts(_jobs(workflow)["backend"])


def test_every_terraform_root_is_checked(workflow: dict[Any, Any]) -> None:
    on_disk = {
        path.name for path in (REPO / "infra").iterdir() if path.is_dir() and any(path.glob("*.tf"))
    }
    assert len(on_disk) >= 5
    loop = re.search(r"for root in ([\w ]+);", _scripts(_jobs(workflow)["infra"]))
    assert loop, "the infra job must loop over the roots"
    assert set(loop.group(1).split()) == on_disk
