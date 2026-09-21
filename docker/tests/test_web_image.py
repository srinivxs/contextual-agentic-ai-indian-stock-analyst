"""The built frontend image: who it runs as, what is inside it, how big it is, how it caches."""

import json
import re
import shutil
import uuid
from pathlib import Path

from helpers import FRONTEND, LABEL, docker, inspect_json, local_secret_values, output

SIZE_FILE = Path(__file__).with_name("image-size-ceilings.json")
HTML = "/usr/share/nginx/html"

PRESENT = [f"{HTML}/index.html", f"{HTML}/404.html", f"{HTML}/stocks/index.html", f"{HTML}/_next"]
ABSENT = [
    f"{HTML}/.env",
    "/app",
    "/build",
    "/usr/local/bin/node",  # the runtime is nginx only: no Node, no npm
    "/usr/local/bin/npm",
    "/node_modules",
    "/package.json",
    "/root/.npm",
    "/root/.cache",
]


def run_in_image(image: str, *command: str) -> str:
    return output(
        docker("run", "--rm", "--label", LABEL, "--entrypoint", command[0], image, *command[1:])
    )


def test_the_image_builds(web_image: str) -> None:
    docker("image", "inspect", web_image)


def test_it_runs_as_a_non_root_user(web_image: str) -> None:
    uid = run_in_image(web_image, "id", "-u").strip()
    assert uid.isdigit(), uid
    assert int(uid) != 0, "runs as root"


def test_it_declares_its_port_and_healthcheck(web_image: str) -> None:
    config = inspect_json(web_image, "{{json .Config}}")
    assert isinstance(config, dict)
    assert "8080/tcp" in config["ExposedPorts"]
    assert "8080" in " ".join(config["Healthcheck"]["Test"])
    names = {entry.split("=", 1)[0] for entry in config["Env"]}
    assert not {n for n in names if any(k in n for k in ("SECRET", "PASSWORD", "TOKEN", "CLIENT"))}


def test_it_contains_the_built_site_and_nothing_it_should_not(web_image: str) -> None:
    probe = (
        "for p in {present}; do test -e $p || echo MISSING:$p; done; "
        "for p in {absent}; do test -e $p && echo UNEXPECTED:$p; done; "
        "find / -xdev \\( -name '.env' -o -name '.env.*' -o -name '*.map' \\) 2>/dev/null "
        "| sed 's/^/UNEXPECTED:/'; echo checked"
    ).format(present=" ".join(PRESENT), absent=" ".join(ABSENT))
    result = run_in_image(web_image, "sh", "-c", probe)
    assert result.strip().splitlines() == ["checked"], result


def test_the_site_files_cannot_be_modified_by_the_user_running_nginx(web_image: str) -> None:
    result = run_in_image(
        web_image, "sh", "-c", f"test ! -w {HTML}/index.html && test ! -w {HTML} && echo LOCKED"
    )
    assert result.strip() == "LOCKED"


def test_no_secret_is_recorded_in_the_image_history(web_image: str) -> None:
    history = output(docker("history", "--no-trunc", "--format", "{{.CreatedBy}}", web_image))
    leaked = [key for key, value in local_secret_values().items() if value in history]
    assert leaked == [], f"values of {leaked} appear in the image history"


def _unpacked_size_mb(image: str) -> float:
    history = docker("history", "--human=false", "--format", "{{.Size}}", image).stdout
    return sum(int(line) for line in history.split()) / 1_000_000


def test_the_image_stays_within_its_documented_size_ceiling(web_image: str) -> None:
    """Set from the first measured clean build, with a written justification (as for the api)."""
    assert SIZE_FILE.is_file()
    sizes = json.loads(SIZE_FILE.read_text(encoding="utf-8"))
    assert "frontend" in sizes, "no baseline yet: measure the first clean build and record it"
    entry = sizes["frontend"]
    assert entry["justification"].strip(), "a ceiling needs a written justification"
    assert entry["ceiling_mb"] <= entry["baseline_mb"] * 1.25, (
        "the ceiling drifted from the baseline"
    )
    actual = _unpacked_size_mb(web_image)
    assert actual <= entry["ceiling_mb"], (
        f"image is {actual:.0f} MB, over the {entry['ceiling_mb']} MB ceiling"
    )


def test_a_source_change_reuses_the_cached_dependency_install(daemon: None, tmp_path: Path) -> None:
    """`npm ci` runs after only the manifests are copied, so editing a page skips the install."""
    context = tmp_path / "frontend"
    shutil.copytree(
        FRONTEND,
        context,
        ignore=shutil.ignore_patterns("node_modules", ".next", "out", ".npm-cache", ".env*"),
    )
    tags = [f"stock-analyst-web-p6cache:{uuid.uuid4().hex[:8]}" for _ in range(2)]
    try:
        docker("build", "--label", LABEL, "-t", tags[0], str(context), timeout=1200)
        with (context / "src" / "app" / "page.tsx").open("a", encoding="utf-8") as handle:
            handle.write("\n// a source-only change\n")
        second = docker(
            "build", "--progress=plain", "--label", LABEL, "-t", tags[1], str(context), timeout=1200
        )
        log = second.stdout + second.stderr
        step = re.search(r"#(\d+) \[[^\]]*\] RUN [^\n]*npm ci", log)
        assert step, "the build log has no `npm ci` step"
        assert f"#{step.group(1)} CACHED" in log, "npm ci re-ran after a source-only change"
        assert re.search(r"#\d+ \[[^\]]*\] RUN [^\n]*npm run build", log)
    finally:
        docker("rmi", "-f", *tags, check=False)
