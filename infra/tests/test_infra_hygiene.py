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
ROOTS = ("preflight", "bootstrap")
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
    """The account id is not a secret, but it stays out of Git (it arrives as a variable)."""
    files = infra_files(*TEXT_SUFFIXES)
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
