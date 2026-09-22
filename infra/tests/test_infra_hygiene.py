"""Repository hygiene for the Terraform code (P7a, step C).

These tests read files and ask git. They need no Terraform and no AWS, so they are safe to run
anywhere. Each one fails, never passes vacuously, when the code it checks does not exist yet.

Run from the repository root:
    backend\\.venv\\Scripts\\python -m pytest infra/tests
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INFRA = REPO / "infra"
ROOTS = ("preflight", "bootstrap", "stack")
# Local caches and the provider download folders: never scanned, never committed.
SKIPPED_DIRS = {".terraform", ".terraform-plugin-cache"}
TEXT_SUFFIXES = (".tf", ".tftest.hcl", ".hcl", ".example", ".md", ".json")

EXPECTED_FILES = {
    "preflight": ["versions.tf", "main.tf", "outputs.tf", "tests/credentials.tftest.hcl"],
    "bootstrap": [
        "versions.tf",
        "main.tf",
        "variables.tf",
        "outputs.tf",
        "tests/state_bucket.tftest.hcl",
    ],
    "stack": [
        "versions.tf",
        "main.tf",
        "backend.tf",
        "backend.hcl.example",
        "variables.tf",
        "network.tf",
        "security.tf",
        "database.tf",
        "secrets.tf",
        "registry.tf",
        "identity.tf",
        "loadbalancer.tf",
        "compute.tf",
        "outputs.tf",
        "tests/network.tftest.hcl",
        "tests/data_and_identity.tftest.hcl",
        "tests/compute.tftest.hcl",
    ],
}


def infra_files(*suffixes: str) -> list[Path]:
    """Files under infra/ with the given suffixes, skipping caches and this test folder itself.

    infra/tests/ is skipped because it contains the very patterns being searched for. The
    Terraform test files inside each root (infra/<root>/tests/*.tftest.hcl) ARE scanned.
    """
    found = []
    for path in INFRA.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(INFRA).parts
        if parts[0] == "tests" or SKIPPED_DIRS & set(parts):
            continue
        if path.name == ".terraform.lock.hcl":  # long hashes, not code
            continue
        if path.name.endswith(suffixes):
            found.append(path)
    return sorted(found)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False, timeout=30
    )


def is_ignored(relative: str) -> bool:
    """Would git ignore this path? Judged by the ignore patterns alone (the path need not exist)."""
    result = git("check-ignore", "-q", "--no-index", relative)
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


def block_body(text: str, header: str) -> str | None:
    """The text between the braces of the first block whose header matches the regex."""
    match = re.search(header, text)
    if match is None:
        return None
    start = text.index("{", match.end())
    depth = 0
    for position in range(start, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : position]
    return None


# --- the code exists, with the shape we designed -----------------------------------------------


@pytest.mark.parametrize("root", ROOTS)
def test_each_root_has_the_files_the_design_names(root: str) -> None:
    missing = [name for name in EXPECTED_FILES[root] if not (INFRA / root / name).is_file()]
    assert not missing, f"infra/{root} is missing: {missing}"


def test_the_preflight_root_only_reads_and_never_creates() -> None:
    main = INFRA / "preflight" / "main.tf"
    assert main.is_file(), "infra/preflight/main.tf does not exist yet"
    text = main.read_text(encoding="utf-8")
    assert not re.search(r'^\s*resource\s+"', text, re.MULTILINE), (
        "the preflight must contain data sources only"
    )
    assert re.search(r'^\s*data\s+"aws_caller_identity"', text, re.MULTILINE)
    assert re.search(r'^\s*data\s+"aws_region"', text, re.MULTILINE)


def test_the_state_bucket_cannot_be_destroyed_by_accident() -> None:
    main = INFRA / "bootstrap" / "main.tf"
    assert main.is_file(), "infra/bootstrap/main.tf does not exist yet"
    body = block_body(main.read_text(encoding="utf-8"), r'resource\s+"aws_s3_bucket"\s+"state"')
    assert body is not None, 'no resource "aws_s3_bucket" "state" in infra/bootstrap/main.tf'
    assert re.search(r"prevent_destroy\s*=\s*true", body), (
        "the state bucket needs lifecycle { prevent_destroy = true }"
    )


# --- Terraform and provider versions ------------------------------------------------------------


@pytest.mark.parametrize("root", ROOTS)
def test_terraform_1_11_or_newer_is_required(root: str) -> None:
    versions = INFRA / root / "versions.tf"
    assert versions.is_file(), f"infra/{root}/versions.tf does not exist"
    match = re.search(r'required_version\s*=\s*"([^"]+)"', versions.read_text(encoding="utf-8"))
    assert match, "no required_version"
    minimum = re.search(r">=\s*(\d+)\.(\d+)", match.group(1))
    assert minimum, f"required_version {match.group(1)!r} has no '>=' lower bound"
    assert (int(minimum.group(1)), int(minimum.group(2))) >= (1, 11)


@pytest.mark.parametrize("root", ROOTS)
def test_the_aws_provider_is_pinned_to_a_major_version(root: str) -> None:
    versions = INFRA / root / "versions.tf"
    assert versions.is_file(), f"infra/{root}/versions.tf does not exist"
    assert re.search(r'version\s*=\s*"~>\s*6\.\d+"', versions.read_text(encoding="utf-8"))


@pytest.mark.parametrize("root", ROOTS)
def test_the_provider_lock_file_is_tracked_by_git(root: str) -> None:
    """`terraform init` writes it; it must be committed so everyone gets the same provider build."""
    result = git("ls-files", "--error-unmatch", f"infra/{root}/.terraform.lock.hcl")
    assert result.returncode == 0, f"infra/{root}/.terraform.lock.hcl is not tracked by git yet"


# --- what git must ignore, and what it must not -------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "infra/bootstrap/terraform.tfstate",
        "infra/bootstrap/terraform.tfstate.backup",
        "infra/stack/terraform.tfvars",
        "infra/stack/.terraform/providers/registry.terraform.io/hashicorp/aws/mirror",
        "infra/stack/tfplan",
        "infra/stack/backend.hcl",
        "infra/.terraform-plugin-cache/registry.terraform.io/hashicorp/aws/mirror",
    ],
)
def test_local_terraform_artifacts_are_ignored_by_git(path: str) -> None:
    assert is_ignored(path), (
        f"{path} is not git-ignored (state, variables and caches never go in git)"
    )


@pytest.mark.parametrize(
    "path",
    [
        "infra/bootstrap/.terraform.lock.hcl",
        "infra/stack/terraform.tfvars.example",
        "infra/stack/backend.hcl.example",
    ],
)
def test_files_that_must_be_committed_are_not_ignored(path: str) -> None:
    assert not is_ignored(path), f"{path} is git-ignored, but it must be committed"


def test_no_state_variable_or_backend_file_is_tracked() -> None:
    tracked = git("ls-files", "infra").stdout.split()
    assert tracked, "nothing is tracked under infra/ yet"
    bad = [
        p
        for p in tracked
        if re.search(r"\.tfstate|\.tfvars$|/backend\.hcl$|/\.terraform/|tfplan", p)
    ]
    assert not bad, f"tracked files that must stay local: {bad}"


# --- no secrets, no account numbers ---------------------------------------------------------------

SECRET_PATTERNS = {
    "AWS access key id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "AWS secret access key assignment": r"(?i)aws_secret_access_key\s*[=:]\s*\S+",
    "AWS session token assignment": r"(?i)aws_session_token\s*[=:]\s*\S+",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "Google client secret": r"GOCSPX-[A-Za-z0-9_-]{6,}",
    "hard-coded credential literal": (
        r'(?i)\b(?:password|secret|token)\w*\s*=\s*"[^"${\s][^"\s]{7,}"'
    ),
}


def test_no_secret_shaped_material_is_written_under_infra() -> None:
    files = infra_files(*TEXT_SUFFIXES)
    assert files, "no infra files to scan yet"
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if re.search(pattern, text):
                hits.append(f"{path.relative_to(REPO)}: {name}")
    assert not hits, f"secret-shaped material: {hits}"


def test_no_real_twelve_digit_account_id_is_written_under_infra() -> None:
    """The account id is not a secret, but it stays out of GIT (it arrives as a variable).

    Files git ignores are exempt, because the rule is about what gets committed, not what sits on
    the disk. infra/stack/backend.hcl is the case that matters: it is generated from the
    bootstrap's output, it necessarily carries the bucket name, and the bucket name necessarily
    carries the account id. It can never be committed, so it can never leak.
    """
    files = [
        path
        for path in infra_files(*TEXT_SUFFIXES)
        if not is_ignored(path.relative_to(REPO).as_posix())
    ]
    assert files, "no infra files to scan yet"
    hits = []
    for path in files:
        for number in re.findall(r"\b\d{12}\b", path.read_text(encoding="utf-8", errors="replace")):
            if number != "123456789012":  # the obvious placeholder used in examples
                hits.append(f"{path.relative_to(REPO)}: {number}")
    assert not hits, f"a real-looking account id is written down: {hits}"


# --- the design's "must not" list, enforced ------------------------------------------

FORBIDDEN_TYPES = [
    # decided against (ADR 004 / 008 / the approved P7 design)
    "aws_nat_gateway",
    "aws_vpc_endpoint",
    "aws_wafv2_web_acl",
    "aws_secretsmanager_secret",
    "aws_dynamodb_table",
    "aws_lambda_function",
    "aws_api_gateway_rest_api",
    "aws_apigatewayv2_api",
    "aws_sqs_queue",
    "aws_elasticache_cluster",
    "aws_opensearch_domain",
    "aws_eks_cluster",
    "aws_db_proxy",
    "aws_rds_cluster",
    "aws_instance",
    "aws_appautoscaling_target",
    "aws_cloudwatch_metric_alarm",
    "aws_cloudwatch_dashboard",
    "aws_route53_zone",
    "aws_acm_certificate",
    # the owner's instructions: no new IAM users or keys; the existing Glue catalog stays put
    "aws_iam_user",
    "aws_iam_access_key",
    "aws_glue_catalog_database",
    "aws_glue_crawler",
]


@pytest.mark.parametrize("resource_type", FORBIDDEN_TYPES)
def test_forbidden_resource_types_are_never_declared(resource_type: str) -> None:
    files = infra_files(".tf")
    assert files, "no .tf files to scan yet"
    pattern = re.compile(rf'^\s*(?:resource|data)\s+"{resource_type}"', re.MULTILINE)
    offenders = [str(p.relative_to(REPO)) for p in files if pattern.search(p.read_text("utf-8"))]
    assert not offenders, f"{resource_type} is forbidden by the design; found in {offenders}"


def test_the_preexisting_demo_bucket_is_never_mentioned() -> None:
    files = infra_files(*TEXT_SUFFIXES)
    assert files, "no infra files to scan yet"
    offenders = [
        str(p.relative_to(REPO)) for p in files if "srinivas-demo-s3-v12" in p.read_text("utf-8")
    ]
    assert not offenders, (
        f"the pre-existing bucket must stay untouched, but is named in {offenders}"
    )


def test_state_locking_never_involves_dynamodb() -> None:
    files = infra_files(".tf")
    assert files, "no .tf files to scan yet"
    offenders = [
        str(p.relative_to(REPO)) for p in files if "dynamodb" in p.read_text("utf-8").lower()
    ]
    assert not offenders, f"DynamoDB is off-limits (S3-native locking replaces it): {offenders}"


def test_only_the_mumbai_region_is_ever_written_in_the_code() -> None:
    files = infra_files(".tf")
    assert files, "no .tf files to scan yet"
    regions = re.compile(r'"((?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d)"')
    offenders = []
    for path in files:
        for region in regions.findall(path.read_text("utf-8")):
            if region != "ap-south-1":
                offenders.append(f"{path.relative_to(REPO)}: {region}")
    assert not offenders, f"only ap-south-1 is allowed: {offenders}"


def test_the_bootstrap_root_keeps_its_own_state_locally() -> None:
    """It creates the bucket that would hold its own state, so it cannot use that bucket."""
    files = [p for p in infra_files(".tf") if "bootstrap" in p.relative_to(INFRA).parts]
    assert files, "no infra/bootstrap .tf files yet"
    offenders = [
        str(p.relative_to(REPO))
        for p in files
        if re.search(r'backend\s+"s3"', p.read_text("utf-8"))
    ]
    assert not offenders, f"the bootstrap must not use the S3 backend: {offenders}"


# --- the application stack (P7b): state, routing and the trust chain ------------------------------


def stack_file(name: str) -> str:
    """The text of a file in infra/stack, failing clearly when it has not been written yet."""
    path = INFRA / "stack" / name
    assert path.is_file(), f"infra/stack/{name} does not exist yet"
    return path.read_text(encoding="utf-8")


def test_the_stack_keeps_its_state_in_the_bucket_the_bootstrap_made() -> None:
    """The backend block is empty on purpose: settings arrive from the git-ignored backend.hcl."""
    body = block_body(stack_file("backend.tf"), r'backend\s+"s3"')
    assert body is not None, 'infra/stack/backend.tf has no backend "s3" block'
    inline = [
        setting
        for setting in ("bucket", "key", "region", "dynamodb_table")
        if re.search(rf"^\s*{setting}\s*=", body, re.MULTILINE)
    ]
    assert not inline, f"backend settings belong in backend.hcl, not in the code: {inline}"


def test_the_backend_example_shows_how_to_point_at_the_state_bucket() -> None:
    text = stack_file("backend.hcl.example")
    missing = [
        key for key in ("bucket", "key", "region", "encrypt", "use_lockfile") if key not in text
    ]
    assert not missing, f"backend.hcl.example must show {missing}"
    assert "dynamodb" not in text.lower(), "locking uses the S3 lock file, nothing else"


def test_the_stack_never_creates_a_second_state_bucket() -> None:
    """The bootstrap's bucket is the only one; the stack reuses it and must never make its own."""
    files = [p for p in infra_files(".tf") if "stack" in p.relative_to(INFRA).parts]
    assert files, "no infra/stack .tf files yet"
    pattern = re.compile(
        r'^\s*resource\s+"aws_s3_bucket"\s+"(?:state|tfstate|terraform_state)"', re.MULTILINE
    )
    offenders = [str(p.relative_to(REPO)) for p in files if pattern.search(p.read_text("utf-8"))]
    assert not offenders, f"the stack must reuse the bootstrap's state bucket: {offenders}"


def test_only_the_public_route_table_ever_gets_a_route() -> None:
    """The isolated subnets have no way out, and no route may quietly give them one."""
    text = stack_file("network.tf")
    names = re.findall(r'resource\s+"aws_route"\s+"(\w+)"', text)
    assert names, 'no resource "aws_route" in infra/stack/network.tf'
    for name in names:
        body = block_body(text, rf'resource\s+"aws_route"\s+"{name}"')
        assert body is not None
        assert "aws_route_table.public" in body, (
            f"aws_route.{name} must belong to the public route table"
        )
        assert "nat_gateway_id" not in body, "there is no NAT gateway in this design (ADR 004/008)"


def test_no_security_group_rule_lets_the_whole_internet_in() -> None:
    """Inbound is always from a prefix list or another security group, never from 0.0.0.0/0."""
    text = stack_file("security.tf")
    names = re.findall(r'resource\s+"aws_vpc_security_group_ingress_rule"\s+"(\w+)"', text)
    assert names, "no inbound security group rules found"
    for name in names:
        body = block_body(text, rf'resource\s+"aws_vpc_security_group_ingress_rule"\s+"{name}"')
        assert body is not None
        assert "0.0.0.0/0" not in body, f"inbound rule {name} is open to the whole internet"


def test_security_group_descriptions_use_only_characters_aws_accepts() -> None:
    """AWS accepts only this character set in a security group description, and enforces it at
    APPLY time. A mock provider does not, so an apostrophe in "Google's" sailed through every
    offline test and then failed the real apply with 22 of 23 resources already created.
    """
    text = stack_file("security.tf")
    descriptions = re.findall(r'^\s*description\s*=\s*"([^"]*)"', text, re.MULTILINE)
    assert descriptions, "no descriptions found in infra/stack/security.tf"
    allowed = re.compile(r"^[a-zA-Z0-9. _\-:/()#,@\[\]+=&;{}!$*]*$")
    rejected = [d for d in descriptions if not allowed.match(d) or len(d) >= 256]
    assert not rejected, f"AWS will reject these descriptions at apply time: {rejected}"


def test_the_vpcs_own_default_security_group_is_left_alone() -> None:
    """Terraform manages only groups this project creates. The VPC's default group is not adopted:
    nothing is ever placed in it and it is deleted with the VPC, so managing it would mean changing
    a resource we did not create for no gain."""
    files = [p for p in infra_files(".tf") if "stack" in p.relative_to(INFRA).parts]
    assert files, "no infra/stack .tf files yet"
    pattern = re.compile(r'^\s*resource\s+"aws_default_security_group"', re.MULTILINE)
    offenders = [str(p.relative_to(REPO)) for p in files if pattern.search(p.read_text("utf-8"))]
    assert not offenders, f"the VPC's default security group must not be managed: {offenders}"


# --- the data tier (P7c): secrets that never reach the state file, and the identity boundary ------


def test_the_stack_pins_the_random_provider() -> None:
    """Only used to generate the passwords as ephemeral values. 3.9 is the first release with it."""
    text = stack_file("versions.tf")
    assert re.search(r'source\s*=\s*"hashicorp/random"', text), (
        "the random provider is not declared"
    )
    assert re.search(r'version\s*=\s*"~>\s*3\.\d+"', text), (
        "the random provider is not pinned to 3.x"
    )


def test_no_secret_is_ever_written_with_the_stateful_attribute() -> None:
    """`password` on aws_db_instance and `value` on aws_ssm_parameter are documented as being
    stored in the state file in PLAIN TEXT. Only the write-only forms may be used, and there must
    be no quiet fallback to the stateful ones if a provider version misbehaves.
    """
    database = stack_file("database.tf")
    assert not re.search(r"^\s*password\s*=", database, re.MULTILINE), (
        "database.tf must use password_wo, never the stateful password attribute"
    )
    assert re.search(r"^\s*password_wo\s*=", database, re.MULTILINE), (
        "database.tf must set password_wo"
    )
    assert re.search(r"^\s*password_wo_version\s*=", database, re.MULTILINE), (
        "password_wo needs password_wo_version, or the value is never re-sent"
    )

    secrets = stack_file("secrets.tf")
    assert not re.search(r"^\s*value\s*=", secrets, re.MULTILINE), (
        "secrets.tf must use value_wo, never the stateful value attribute"
    )
    assert re.search(r"^\s*value_wo\s*=", secrets, re.MULTILINE), "secrets.tf must set value_wo"
    assert re.search(r"^\s*value_wo_version\s*=", secrets, re.MULTILINE), (
        "value_wo needs value_wo_version, or the value is never re-sent"
    )


def test_the_database_stays_on_postgresql_major_sixteen() -> None:
    """A change of major version is a different database, and must never happen quietly."""
    text = stack_file("database.tf")
    assert re.search(r'^\s*engine\s*=\s*"postgres"\s*$', text, re.MULTILINE), (
        'engine must be "postgres"'
    )
    assert re.search(r'^\s*engine_version\s*=\s*"16"\s*$', text, re.MULTILINE), (
        'engine_version must be exactly "16": major pinned, minor left to AWS'
    )
    assert re.search(r'^\s*family\s*=\s*"postgres16"\s*$', text, re.MULTILINE), (
        'the parameter group family must be "postgres16"'
    )
    others = {major for major in re.findall(r'"postgres(\d+)"', text) if major != "16"}
    assert not others, f"another PostgreSQL major version is named in database.tf: {sorted(others)}"


def test_no_iam_policy_grants_a_wildcard_resource() -> None:
    """Every statement names the exact ARN it applies to. "*" would quietly undo the boundary
    between the api role and the admin database URL."""
    text = stack_file("identity.tf")
    assert not re.search(r'Resource\s*=\s*\[?\s*"\*"', text), (
        'an IAM statement in identity.tf uses Resource = "*"'
    )


def test_no_output_can_carry_a_secret() -> None:
    """Outputs are printed by `terraform output`, appear in plan output and are stored in state."""
    text = stack_file("outputs.tf")
    leaks = [
        token for token in ("ephemeral.", "random_password", "password", ".value") if token in text
    ]
    assert not leaks, f"outputs.tf refers to something secret: {leaks}"


# --- the compute tier (P7d): the boundary again, and the switch that starts billing ---------------


def test_the_api_task_definition_never_names_the_admin_parameter() -> None:
    """The IAM boundary would still hold if this were wrong, but the task would fail to start and
    the intent would be wrong. Asking for the admin URL must not even be expressible here."""
    text = stack_file("compute.tf")
    body = block_body(text, r'resource\s+"aws_ecs_task_definition"\s+"api"')
    assert body is not None, 'no resource "aws_ecs_task_definition" "api" in compute.tf'
    assert "migration_parameter" not in body, (
        "the API task definition must never reference the admin database parameter"
    )
    migrate = block_body(text, r'resource\s+"aws_ecs_task_definition"\s+"migrate"')
    assert migrate is not None, "the migration task definition is missing"
    assert "migration_parameter" in migrate, (
        "the migration task definition must reference the admin parameter"
    )


def test_the_tasks_are_built_for_the_architecture_of_the_image() -> None:
    """The image is linux/amd64. An ARM task fails at start with a message about the platform."""
    text = stack_file("compute.tf")
    definitions = len(re.findall(r'resource\s+"aws_ecs_task_definition"', text))
    assert definitions >= 3, "expected at least the api, migrate and provision task definitions"
    assert text.count("X86_64") == definitions, "every task definition must declare X86_64"
    assert "ARM64" not in text, "ARM64 would not match the image that is actually built"


def test_the_service_never_defaults_to_running_tasks() -> None:
    """The load balancer bills regardless, but Fargate should not start without being asked."""
    text = stack_file("variables.tf")
    body = block_body(text, r'variable\s+"desired_count"')
    assert body is not None, 'no variable "desired_count" in variables.tf'
    assert re.search(r"^\s*default\s*=\s*0\s*$", body, re.MULTILINE), (
        "desired_count must default to 0, so applying the stack starts no compute"
    )


# --- a container command must name something the image actually contains ------------------------
#
# This is the class of bug the migration task shipped with: `command = ["migrate"]` read like the
# Compose service name, but the image has no ENTRYPOINT, so `command` replaces the CMD outright and
# there is no `migrate` executable to run. It would have failed with `exec: "migrate": not found`
# about ten minutes into an apply, and no mock provider can catch it -- the plan is perfectly valid.
# So the check has to be made here, against the image's real contents.

BACKEND = REPO / "backend"


def task_definition_bodies() -> dict[str, str]:
    """Every aws_ecs_task_definition in compute.tf, by name."""
    text = stack_file("compute.tf")
    names = re.findall(r'resource\s+"aws_ecs_task_definition"\s+"(\w+)"', text)
    bodies = {}
    for name in names:
        body = block_body(text, rf'resource\s+"aws_ecs_task_definition"\s+"{name}"')
        assert body is not None, f"could not read the body of task definition {name}"
        bodies[name] = body
    return bodies


def container_command(body: str) -> list[str]:
    """The argv of a container definition, or an empty list when it keeps the image's CMD."""
    match = re.search(r"command\s*=\s*\[(.*?)\]", body, re.DOTALL)
    if match is None:
        return []
    return re.findall(r'"([^"]*)"', match.group(1))


def test_the_backend_image_still_has_no_entrypoint() -> None:
    """The premise every `command` below depends on.

    With no ENTRYPOINT, `command` replaces the image's CMD entirely, so it must be a complete argv.
    If an ENTRYPOINT is ever added, every task definition's command becomes arguments to it instead,
    and each one has to be re-read.
    """
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*ENTRYPOINT", dockerfile, re.MULTILINE), (
        "the backend image gained an ENTRYPOINT; every task definition's command now means "
        "something different"
    )


def test_every_task_command_names_something_the_image_contains() -> None:
    for name, body in task_definition_bodies().items():
        command = container_command(body)
        if not command:
            continue  # keeps the image's own CMD (uvicorn), which is checked by the Dockerfile
        if command[0] == "python":
            assert command[1] == "-m", f"{name}: only `python -m <module>` is understood here"
            module = BACKEND / "src" / (command[2].replace(".", "/") + ".py")
            assert module.is_file(), (
                f"{name} runs `{' '.join(command)}`, but {module.relative_to(REPO)} does not exist"
            )
        elif command[0] == "alembic":
            assert (BACKEND / "alembic.ini").is_file(), (
                f"{name} runs alembic, but backend/alembic.ini does not exist"
            )
            assert command[1:] == ["upgrade", "head"], (
                f"{name}: the only alembic command we run in AWS is `upgrade head`"
            )
        else:
            pytest.fail(
                f"{name} runs `{command[0]}`, which this test does not know how to verify. "
                "Add a case here rather than trusting that the image can run it."
            )


def test_a_module_run_with_python_m_is_given_the_import_path() -> None:
    """WORKDIR is /app and the code is at /app/src.

    Uvicorn is told with `--app-dir src`; `python -m` has no such flag, so without PYTHONPATH the
    container exits with ModuleNotFoundError before running a line of ours.
    """
    for name, body in task_definition_bodies().items():
        if container_command(body)[:2] != ["python", "-m"]:
            continue
        assert re.search(r'name\s*=\s*"PYTHONPATH"\s*,\s*value\s*=\s*"/app/src"', body), (
            f"{name} runs a module with `python -m` but does not set PYTHONPATH=/app/src"
        )
