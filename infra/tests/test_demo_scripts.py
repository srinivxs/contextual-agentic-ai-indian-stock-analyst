"""Guards for scripts/demo-up.ps1 and scripts/demo-down.ps1 (P8).

The owner runs these against the real account, so the rules that keep a slip cheap are checked here:
a destroy can only ever reach infra/stack, every apply and destroy waits for a typed `yes`, no plan
file (which holds the Google secret in plaintext) is written, and nothing secret is printed.

These tests read the scripts as text. That the scripts actually work is proven by running them.

Run from the repository root:
    backend\\.venv\\Scripts\\python -m pytest infra/tests
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
NAMES = ("demo-up.ps1", "demo-down.ps1", "demo-common.ps1")


def _code(name: str) -> str:
    """The script without comments or its help block, so prose cannot satisfy or break a test."""
    path = SCRIPTS / name
    if not path.is_file():
        pytest.fail(f"scripts/{name} does not exist")
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


@pytest.mark.parametrize("name", NAMES)
def test_nothing_is_applied_or_destroyed_without_a_typed_yes(name: str) -> None:
    assert "auto-approve" not in _code(name)


@pytest.mark.parametrize("name", NAMES)
def test_no_plan_file_is_written(name: str) -> None:
    # `terraform plan -out=tfplan` saves the Google client secret in plaintext. (`--output` is the
    # AWS CLI's flag and is fine, hence the look-behind.)
    assert not re.search(r"(?<![-\w])-out", _code(name))


def test_terraform_can_only_reach_the_stack_and_the_edge() -> None:
    common = _code("demo-common.ps1")
    assert "[ValidateSet('stack', 'edge')][string]$Root" in common
    for name in NAMES:
        code = _code(name)
        for permanent in ("cicd", "bootstrap"):
            assert f"infra\\{permanent}" not in code
            assert f"infra/{permanent}" not in code
        assert re.findall(r"&\s*terraform\b", code) == [] or name == "demo-common.ps1", (
            "terraform is only ever called through Invoke-Terraform or Get-StackOutputs"
        )


def test_the_only_destroy_is_of_the_stack() -> None:
    destroys = [
        line
        for name in NAMES
        for line in _code(name).splitlines()
        if "destroy" in line and "Invoke-Terraform" in line
    ]
    assert destroys == ["Invoke-Terraform -Root 'stack' -Arguments @('destroy')"]
    assert "destroy" not in _code("demo-up.ps1")


def test_the_api_starts_only_after_the_role_and_the_schema_exist() -> None:
    up = _code("demo-up.ps1")
    provision = up.index("stock-analyst-demo-provision")
    migrate = up.index("stock-analyst-demo-migrate")
    start = up.index("'--desired-count', '1'")
    assert provision < start
    assert migrate < start


def test_a_one_off_task_must_exit_zero() -> None:
    common = _code("demo-common.ps1")
    assert "tasks-stopped" in common
    assert "if ($code -ne '0')" in common


def test_the_edge_is_pointed_at_the_load_balancer_the_stack_just_created() -> None:
    up = _code("demo-up.ps1")
    assert "Invoke-Terraform -Root 'edge'" in up
    assert '"-var=alb_origin_domain=$($outputs.alb_dns_name)"' in up


def test_the_down_script_proves_nothing_billable_survived() -> None:
    down = _code("demo-down.ps1")
    assert "Test-NothingBillable" in down
    assert "exit 1" in down
    common = _code("demo-common.ps1")
    for service in (
        "ecs', 'list-clusters",
        "describe-db-instances",
        "describe-load-balancers",
        "describe-vpcs",
        "describe-addresses",
        "describe-db-snapshots",
    ):
        assert service in common


@pytest.mark.parametrize("name", NAMES)
def test_secrets_are_never_printed(name: str) -> None:
    for line in _code(name).splitlines():
        if "Write-Host" in line or "Write-Output" in line:
            assert "google_client_secret" not in line.lower()
            assert "TF_VAR_" not in line


@pytest.mark.parametrize("name", NAMES)
def test_it_runs_in_windows_powershell_5(name: str) -> None:
    # 5.1 has no pipeline chain operators, no null-coalescing and no ternary.
    code = _code(name)
    for token in (" && ", " || ", " ?? ", "?."):
        assert token not in code
