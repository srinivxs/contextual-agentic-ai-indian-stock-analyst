"""Tests for .github/scripts/deploy_backend.py (P8c): what the pipeline does to a running stack.

The script never talks to AWS directly: it is handed a function that runs one `aws` command and
returns its JSON. Here that function is a fake that answers from a script of canned replies and
records every call, so each test can say exactly which commands ran and in what order. That is the
whole contract: migrate before rolling out, never roll out after a failed migration, never mistake
"access denied" for "the stack is down".

Run from the repository root:
    backend\\.venv\\Scripts\\python -m pytest infra/tests
"""

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".github" / "scripts" / "deploy_backend.py"

DIGEST = "sha256:" + "a" * 64
REPOSITORY_URL = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend"
PUBLIC_URL = "https://d111111abcdef8.cloudfront.net"
OLD_API = "arn:aws:ecs:ap-south-1:123456789012:task-definition/stock-analyst-demo-api:7"
NEW_API = "arn:aws:ecs:ap-south-1:123456789012:task-definition/stock-analyst-demo-api:8"
NEW_MIGRATE = "arn:aws:ecs:ap-south-1:123456789012:task-definition/stock-analyst-demo-migrate:5"
TASK = "arn:aws:ecs:ap-south-1:123456789012:task/stock-analyst-demo/0123456789abcdef"
NETWORK = {
    "awsvpcConfiguration": {
        "subnets": ["subnet-aaa", "subnet-bbb"],
        "securityGroups": ["sg-task"],
        "assignPublicIp": "ENABLED",
    }
}


def _load() -> ModuleType:
    if not SCRIPT.is_file():
        pytest.fail(f"{SCRIPT.relative_to(REPO)} does not exist")
    spec = importlib.util.spec_from_file_location("deploy_backend", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_backend"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def deploy_backend() -> ModuleType:
    return _load()


def _task_definition(family: str) -> dict[str, Any]:
    """What `describe-task-definition` returns: writable fields plus the read-only ones."""
    return {
        "taskDefinitionArn": f"arn:aws:ecs:ap-south-1:123456789012:task-definition/{family}:4",
        "revision": 4,
        "status": "ACTIVE",
        "requiresAttributes": [{"name": "ecs.capability.execution-role-awslogs"}],
        "compatibilities": ["EC2", "FARGATE"],
        "registeredAt": "2026-09-22T10:00:00+05:30",
        "registeredBy": "arn:aws:iam::123456789012:user/srinivas",
        "family": family,
        "executionRoleArn": "arn:aws:iam::123456789012:role/stock-analyst-demo-api-execution",
        "networkMode": "awsvpc",
        "containerDefinitions": [
            {
                "name": "api",
                "image": f"{REPOSITORY_URL}@sha256:{'0' * 64}",
                "secrets": [{"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:mock"}],
            }
        ],
    }


class FakeAws:
    """Answers `aws` calls from canned replies keyed by (service, operation)."""

    def __init__(self, replies: dict[tuple[str, str], list[Any]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> Any:
        self.calls.append(args)
        queue = self.replies.get((args[0], args[1]))
        if not queue:
            raise AssertionError(f"unexpected aws call: {args}")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply(args) if callable(reply) else reply

    def operations(self) -> list[str]:
        return [" ".join(call[:3] if call[1] == "wait" else call[:2]) for call in self.calls]

    def call(self, service: str, operation: str) -> tuple[str, ...]:
        matches = [c for c in self.calls if c[:2] == (service, operation)]
        assert len(matches) == 1, f"{service} {operation} ran {len(matches)} times"
        return matches[0]


def _service(desired: int = 1, status: str = "ACTIVE") -> dict[str, Any]:
    return {
        "services": [
            {
                "status": status,
                "desiredCount": desired,
                "networkConfiguration": NETWORK,
                "deployments": [{"status": "PRIMARY", "taskDefinition": OLD_API}],
            }
        ],
        "failures": [],
    }


def _parameter(args: tuple[str, ...]) -> dict[str, Any]:
    name = args[args.index("--name") + 1]
    values = {
        "/stock-analyst/demo/ecr_repository_url": REPOSITORY_URL,
        "/stock-analyst/demo/public_base_url": PUBLIC_URL,
    }
    return {"Parameter": {"Value": values[name]}}


def _register(args: tuple[str, ...]) -> dict[str, Any]:
    payload = json.loads(args[args.index("--cli-input-json") + 1])
    arn = NEW_MIGRATE if payload["family"].endswith("migrate") else NEW_API
    return {"taskDefinition": {"taskDefinitionArn": arn}}


def _healthy_stack(**overrides: list[Any]) -> FakeAws:
    replies: dict[tuple[str, str], list[Any]] = {
        ("ecs", "describe-services"): [
            _service(),
            {
                "services": [
                    {"deployments": [{"status": "PRIMARY", "taskDefinition": NEW_API}]},
                ]
            },
        ],
        ("ssm", "get-parameter"): [_parameter],
        ("ecs", "describe-task-definition"): [
            lambda args: {
                "taskDefinition": _task_definition(args[args.index("--task-definition") + 1])
            }
        ],
        ("ecs", "register-task-definition"): [_register],
        ("ecs", "run-task"): [{"tasks": [{"taskArn": TASK}], "failures": []}],
        ("ecs", "wait"): [{}],
        ("ecs", "describe-tasks"): [
            {
                "tasks": [
                    {"stoppedReason": "Essential container exited", "containers": [{"exitCode": 0}]}
                ]
            }
        ],
        ("ecs", "update-service"): [{}],
    }
    for key, value in overrides.items():
        service, operation = key.split(" ")
        replies[(service, operation)] = value
    return FakeAws(replies)


class Recorder:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> None:
        self.urls.append(url)


# --- is there anything to deploy to? --------------------------------------------------------------


def test_a_stack_that_is_down_is_left_alone(deploy_backend: ModuleType) -> None:
    aws = FakeAws(
        {
            ("ecs", "describe-services"): [
                deploy_backend.AwsError(
                    "An error occurred (ClusterNotFoundException) when calling the "
                    "DescribeServices operation: Cluster not found."
                )
            ]
        }
    )
    smoke = Recorder()

    message = deploy_backend.deploy(aws, DIGEST, smoke)

    assert "down" in message
    assert aws.operations() == ["ecs describe-services"]
    assert smoke.urls == []


@pytest.mark.parametrize(
    "reply",
    [
        {"services": [], "failures": [{"reason": "MISSING"}]},
        _service(status="INACTIVE"),
        _service(status="DRAINING"),
    ],
    ids=["missing", "inactive", "draining"],
)
def test_a_service_that_is_not_active_counts_as_down(
    deploy_backend: ModuleType, reply: dict[str, Any]
) -> None:
    aws = FakeAws({("ecs", "describe-services"): [reply]})

    assert "down" in deploy_backend.deploy(aws, DIGEST, Recorder())
    assert aws.operations() == ["ecs describe-services"]


def test_access_denied_fails_the_deploy_instead_of_passing_for_a_stack_that_is_down(
    deploy_backend: ModuleType,
) -> None:
    # The dangerous mistake: treating every error as "nothing to deploy" would turn a broken role
    # into a green pipeline that never deploys anything.
    denied = deploy_backend.AwsError("An error occurred (AccessDeniedException) ...")
    aws = FakeAws({("ecs", "describe-services"): [denied]})

    with pytest.raises(deploy_backend.AwsError):
        deploy_backend.deploy(aws, DIGEST, Recorder())


@pytest.mark.parametrize("digest", ["latest", "sha256:abc", "", "a" * 64])
def test_a_malformed_digest_is_refused_before_anything_is_touched(
    deploy_backend: ModuleType, digest: str
) -> None:
    aws = _healthy_stack()

    with pytest.raises(deploy_backend.DeployFailed):
        deploy_backend.deploy(aws, digest, Recorder())
    assert aws.calls == []


# --- the order of a real deploy -------------------------------------------------------------------


def test_the_migration_runs_first_then_the_rollout_then_the_health_check(
    deploy_backend: ModuleType,
) -> None:
    aws = _healthy_stack()
    smoke = Recorder()

    message = deploy_backend.deploy(aws, DIGEST, smoke)

    assert aws.operations() == [
        "ecs describe-services",
        "ssm get-parameter",
        "ecs describe-task-definition",
        "ecs register-task-definition",
        "ecs describe-task-definition",
        "ecs register-task-definition",
        "ecs run-task",
        "ecs wait tasks-stopped",
        "ecs describe-tasks",
        "ecs update-service",
        "ecs wait services-stable",
        "ecs describe-services",
        "ssm get-parameter",
    ]
    assert smoke.urls == [PUBLIC_URL]
    assert NEW_API in message


def test_the_migration_is_the_new_revision_and_runs_on_the_services_own_network(
    deploy_backend: ModuleType,
) -> None:
    aws = _healthy_stack()
    deploy_backend.deploy(aws, DIGEST, Recorder())

    run = aws.call("ecs", "run-task")
    assert run[run.index("--task-definition") + 1] == NEW_MIGRATE
    assert run[run.index("--cluster") + 1] == "stock-analyst-demo"
    assert json.loads(run[run.index("--network-configuration") + 1]) == NETWORK


def test_the_service_is_moved_to_the_new_api_revision(deploy_backend: ModuleType) -> None:
    aws = _healthy_stack()
    deploy_backend.deploy(aws, DIGEST, Recorder())

    update = aws.call("ecs", "update-service")
    assert update[update.index("--service") + 1] == "stock-analyst-demo-api"
    assert update[update.index("--task-definition") + 1] == NEW_API


def test_new_revisions_carry_the_new_image_by_digest_and_keep_everything_else(
    deploy_backend: ModuleType,
) -> None:
    aws = _healthy_stack()
    deploy_backend.deploy(aws, DIGEST, Recorder())

    registered = [
        json.loads(call[call.index("--cli-input-json") + 1])
        for call in aws.calls
        if call[:2] == ("ecs", "register-task-definition")
    ]
    assert {payload["family"] for payload in registered} == {
        "stock-analyst-demo-migrate",
        "stock-analyst-demo-api",
    }
    for payload in registered:
        assert payload["containerDefinitions"][0]["image"] == f"{REPOSITORY_URL}@{DIGEST}"
        assert payload["containerDefinitions"][0]["secrets"] == [
            {"name": "DATABASE_URL", "valueFrom": "arn:aws:ssm:mock"}
        ]
        assert payload["executionRoleArn"].endswith("stock-analyst-demo-api-execution")
        # register-task-definition rejects the fields AWS itself sets.
        for read_only in (
            "taskDefinitionArn",
            "revision",
            "status",
            "requiresAttributes",
            "compatibilities",
            "registeredAt",
            "registeredBy",
        ):
            assert read_only not in payload


def test_every_container_in_the_task_gets_the_new_image(deploy_backend: ModuleType) -> None:
    """Go-live: the api task carries two containers, api and worker, from the same image. A deploy
    that changed only the first would leave the worker running yesterday's code."""

    def two_containers(args: tuple[str, ...]) -> dict[str, Any]:
        definition = _task_definition(args[args.index("--task-definition") + 1])
        worker = dict(definition["containerDefinitions"][0], name="worker")
        definition["containerDefinitions"].append(worker)
        return {"taskDefinition": definition}

    aws = _healthy_stack(**{"ecs describe-task-definition": [two_containers]})
    deploy_backend.deploy(aws, DIGEST, Recorder())

    for call in aws.calls:
        if call[:2] == ("ecs", "register-task-definition"):
            payload = json.loads(call[call.index("--cli-input-json") + 1])
            assert [c["name"] for c in payload["containerDefinitions"]] == ["api", "worker"]
            assert {c["image"] for c in payload["containerDefinitions"]} == {
                f"{REPOSITORY_URL}@{DIGEST}"
            }


# --- what stops a deploy --------------------------------------------------------------------------


def test_a_failed_migration_never_touches_the_running_service(deploy_backend: ModuleType) -> None:
    aws = _healthy_stack(
        **{
            "ecs describe-tasks": [
                {
                    "tasks": [
                        {
                            "stoppedReason": "Essential container exited",
                            "containers": [{"exitCode": 1}],
                        }
                    ]
                }
            ]
        }
    )

    with pytest.raises(deploy_backend.DeployFailed, match="migration"):
        deploy_backend.deploy(aws, DIGEST, Recorder())
    assert ("ecs", "update-service") not in [call[:2] for call in aws.calls]


def test_a_migration_that_never_ran_its_container_fails_the_deploy(
    deploy_backend: ModuleType,
) -> None:
    # An image that cannot be pulled stops the task with no exit code at all.
    aws = _healthy_stack(
        **{
            "ecs describe-tasks": [
                {"tasks": [{"stoppedReason": "CannotPullContainerError", "containers": [{}]}]}
            ]
        }
    )

    with pytest.raises(deploy_backend.DeployFailed, match="CannotPullContainerError"):
        deploy_backend.deploy(aws, DIGEST, Recorder())
    assert ("ecs", "update-service") not in [call[:2] for call in aws.calls]


def test_a_migration_task_that_could_not_be_placed_fails_the_deploy(
    deploy_backend: ModuleType,
) -> None:
    aws = _healthy_stack(
        **{"ecs run-task": [{"tasks": [], "failures": [{"reason": "RESOURCE:MEMORY"}]}]}
    )

    with pytest.raises(deploy_backend.DeployFailed, match="RESOURCE:MEMORY"):
        deploy_backend.deploy(aws, DIGEST, Recorder())
    assert ("ecs", "update-service") not in [call[:2] for call in aws.calls]


def test_a_rollback_by_the_circuit_breaker_fails_the_deploy(deploy_backend: ModuleType) -> None:
    # `wait services-stable` also succeeds when ECS has rolled back: the service IS stable, on the
    # old revision. Only comparing the primary deployment with what we asked for tells them apart.
    aws = _healthy_stack(
        **{
            "ecs describe-services": [
                _service(),
                {"services": [{"deployments": [{"status": "PRIMARY", "taskDefinition": OLD_API}]}]},
            ]
        }
    )
    smoke = Recorder()

    with pytest.raises(deploy_backend.DeployFailed, match="rolled back"):
        deploy_backend.deploy(aws, DIGEST, smoke)
    assert smoke.urls == []


def test_a_service_scaled_to_zero_gets_the_new_version_but_no_health_check(
    deploy_backend: ModuleType,
) -> None:
    aws = _healthy_stack(**{"ecs describe-services": [_service(desired=0)]})
    smoke = Recorder()

    message = deploy_backend.deploy(aws, DIGEST, smoke)

    assert aws.operations()[-1] == "ecs update-service"
    assert smoke.urls == []
    assert "0" in message


# --- the health check through CloudFront ----------------------------------------------------------


def _fetcher(replies: list[tuple[int, str] | Exception]) -> Callable[[str], tuple[int, str]]:
    def fetch(url: str) -> tuple[int, str]:
        assert url == f"{PUBLIC_URL}/api/readyz"
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return fetch


def test_the_health_check_waits_until_the_api_reports_ready(deploy_backend: ModuleType) -> None:
    slept: list[float] = []
    fetch = _fetcher([OSError("connection reset"), (503, "{}"), (200, '{"status":"ready"}')])

    deploy_backend.wait_until_ready(PUBLIC_URL, fetch=fetch, sleep=slept.append, attempts=5)

    assert len(slept) == 2


def test_the_health_check_gives_up_and_fails_the_deploy(deploy_backend: ModuleType) -> None:
    fetch = _fetcher([(200, "not json"), (502, ""), (200, '{"status":"starting"}')])

    with pytest.raises(deploy_backend.DeployFailed, match="readyz"):
        deploy_backend.wait_until_ready(PUBLIC_URL, fetch=fetch, sleep=lambda _: None, attempts=3)
