"""Deploy one backend image to the running stack, or do nothing if the stack is down (P8c, ADR 017).

Run by the pipeline's deploy job, after the image job pushed the image and handed over its digest:

    IMAGE_DIGEST=sha256:... python3 .github/scripts/deploy_backend.py

THE ORDER, AND WHY
  1. Is the stack up? infra/stack is destroyed after every session, so usually it is not. Then
     there is nothing to do: the image is in ECR, and the next `terraform apply` runs the newest
     image.
     Only "the cluster does not exist" or "the service is not active" means down. Any other error
     (access denied, say) FAILS the job: skipping would hide a broken deploy behind a green tick.
  2. Register new revisions of the api and migrate task definitions: the current ones, with only the
     image replaced, named by digest.
  3. Migrate FIRST, as a one-off task, while the old version keeps serving. Migrations are
     expand/contract (the project notes), so the old code still works on the new schema. If the migration
     fails, stop: the running service was never touched.
  4. Move the service to the new api revision. ECS starts the new task, waits for the load balancer
     to call it healthy, then stops the old one. If it never becomes healthy, the circuit breaker
     rolls back -- and `wait services-stable` still succeeds, on the OLD revision, so the primary
     deployment is compared with what was asked for.
  5. Check /api/readyz through CloudFront: the whole path, from the internet to the database.

Only the standard library and the `aws` CLI (already on the runner) are used. The functions take the
`aws` runner, the health check and the clock as arguments, so the tests replace them with fakes.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

CLUSTER = "stock-analyst-demo"
SERVICE = "stock-analyst-demo-api"
API_FAMILY = "stock-analyst-demo-api"
MIGRATE_FAMILY = "stock-analyst-demo-migrate"
PARAMETER_PREFIX = "/stock-analyst/demo/"

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

# Set by AWS on every revision; register-task-definition refuses them.
READ_ONLY_FIELDS = (
    "taskDefinitionArn",
    "revision",
    "status",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
    "deregisteredAt",
)

Aws = Callable[..., Any]


class AwsError(Exception):
    """An `aws` command failed. The message is its stderr."""


class DeployFailed(Exception):
    """The deploy stopped on purpose. The message says what, and what was left untouched."""


def run_aws(*args: str) -> Any:
    """Run one `aws` command and return its JSON output ({} when it prints nothing, like `wait`)."""
    result = subprocess.run(  # noqa: S603 - a fixed program with arguments, no shell
        ["aws", *args, "--output", "json"],  # noqa: S607 - the aws CLI from the runner's PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AwsError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def deploy(aws: Aws, digest: str, smoke_test: Callable[[str], None]) -> str:
    """Deploy the image with this digest. Returns what happened; raises DeployFailed to stop."""
    if not DIGEST.match(digest):
        raise DeployFailed(f"{digest!r} is not an image digest; nothing was touched.")

    service = _active_service(aws)
    if service is None:
        return "The stack is down: the image is in ECR, and the next apply of infra/stack runs it."

    image = f"{_parameter(aws, 'ecr_repository_url')}@{digest}"
    migrate = _register_with_image(aws, MIGRATE_FAMILY, image)
    api = _register_with_image(aws, API_FAMILY, image)

    _migrate(aws, migrate, service["networkConfiguration"])

    aws(
        "ecs",
        "update-service",
        "--cluster",
        CLUSTER,
        "--service",
        SERVICE,
        "--task-definition",
        api,
    )
    if service["desiredCount"] == 0:
        return f"The service is scaled to 0: it will start {api} when scaled up. No health check."

    aws("ecs", "wait", "services-stable", "--cluster", CLUSTER, "--services", SERVICE)
    primary = _primary_task_definition(aws)
    if primary != api:
        raise DeployFailed(
            f"ECS rolled back to {primary}: the new version never became healthy. "
            "The database is already migrated; the old version keeps serving."
        )

    smoke_test(_parameter(aws, "public_base_url"))
    return f"Deployed {api}."


def _active_service(aws: Aws) -> dict[str, Any] | None:
    try:
        reply = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", SERVICE)
    except AwsError as error:
        if "ClusterNotFoundException" in str(error):
            return None
        raise
    active = [s for s in reply.get("services", []) if s.get("status") == "ACTIVE"]
    return active[0] if active else None


def _parameter(aws: Aws, name: str) -> str:
    reply = aws("ssm", "get-parameter", "--name", PARAMETER_PREFIX + name)
    return str(reply["Parameter"]["Value"])


def _register_with_image(aws: Aws, family: str, image: str) -> str:
    """Register a copy of the family's latest revision with only the image changed."""
    definition = aws("ecs", "describe-task-definition", "--task-definition", family)[
        "taskDefinition"
    ]
    for field in READ_ONLY_FIELDS:
        definition.pop(field, None)
    definition["containerDefinitions"][0]["image"] = image
    reply = aws("ecs", "register-task-definition", "--cli-input-json", json.dumps(definition))
    return str(reply["taskDefinition"]["taskDefinitionArn"])


def _migrate(aws: Aws, task_definition: str, network: dict[str, Any]) -> None:
    started = aws(
        "ecs",
        "run-task",
        "--cluster",
        CLUSTER,
        "--task-definition",
        task_definition,
        "--launch-type",
        "FARGATE",
        "--network-configuration",
        json.dumps(network),
    )
    if not started.get("tasks"):
        raise DeployFailed(
            f"The migration task could not start: {started.get('failures')}. Nothing was changed."
        )
    task = started["tasks"][0]["taskArn"]

    aws("ecs", "wait", "tasks-stopped", "--cluster", CLUSTER, "--tasks", task)
    stopped = aws("ecs", "describe-tasks", "--cluster", CLUSTER, "--tasks", task)["tasks"][0]
    exit_code = stopped["containers"][0].get("exitCode")
    if exit_code != 0:
        raise DeployFailed(
            f"The migration exited with {exit_code} ({stopped.get('stoppedReason')}). "
            "The running version was not touched; read /stock-analyst/demo/migrate in CloudWatch."
        )


def _primary_task_definition(aws: Aws) -> str:
    reply = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", SERVICE)
    deployments = reply["services"][0]["deployments"]
    return str(next(d["taskDefinition"] for d in deployments if d["status"] == "PRIMARY"))


# --- the health check through CloudFront ----------------------------------------------------------


def fetch_url(url: str) -> tuple[int, str]:
    """GET a URL; returns (status, body). Errors with a status are returned, not raised."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - https from SSM
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""


def wait_until_ready(
    base_url: str,
    fetch: Callable[[str], tuple[int, str]] = fetch_url,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 12,
) -> None:
    """Poll /api/readyz until it answers {"status": "ready"}, or fail the deploy."""
    url = f"{base_url}/api/readyz"
    for attempt in range(attempts):
        if attempt:
            sleep(10)
        try:
            status, body = fetch(url)
            if status == 200 and json.loads(body).get("status") == "ready":
                return
        except (OSError, ValueError):
            continue  # not answering yet, or not JSON yet: try again
    raise DeployFailed(f"{url} never reported ready after {attempts} attempts.")


def main() -> int:
    try:
        print(deploy(run_aws, os.environ.get("IMAGE_DIGEST", ""), wait_until_ready))
    except (AwsError, DeployFailed) as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
