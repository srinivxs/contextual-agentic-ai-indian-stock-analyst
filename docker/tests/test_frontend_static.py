"""What frontend/Dockerfile, .dockerignore and nginx.conf must say. Read as text: no Docker needed.

Each test fails (never passes vacuously) when the file does not exist yet.
"""

import re

import pytest

from helpers import FRONTEND, Instruction, final_stage, parse_dockerfile

DOCKERFILE = FRONTEND / "Dockerfile"
DOCKERIGNORE = FRONTEND / ".dockerignore"
NGINX_CONF = FRONTEND / "nginx.conf"


def nginx_directives() -> list[str]:
    """nginx.conf with comments and blank lines removed, one directive or block header per line."""
    assert NGINX_CONF.is_file(), "frontend/nginx.conf does not exist"
    lines = []
    for raw in NGINX_CONF.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


@pytest.fixture(scope="module")
def instructions() -> list[Instruction]:
    assert DOCKERFILE.is_file(), "frontend/Dockerfile does not exist"
    return parse_dockerfile(DOCKERFILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def last(instructions: list[Instruction]) -> list[Instruction]:
    return final_stage(instructions)


# --- the Dockerfile ----------------------------------------------------------------------


def test_the_dockerfile_and_nginx_conf_use_unix_line_endings() -> None:
    for path in (DOCKERFILE, NGINX_CONF, DOCKERIGNORE):
        assert path.is_file(), f"{path.relative_to(FRONTEND.parent)} does not exist"
        assert b"\r" not in path.read_bytes(), f"CRLF in {path.name} (.gitattributes forces LF)"


def test_it_is_a_multi_stage_build_and_the_final_stage_is_nginx_not_node(
    instructions: list[Instruction], last: list[Instruction]
) -> None:
    froms = [i for i in instructions if i.name == "FROM"]
    assert len(froms) >= 2, "a Node builder stage plus an nginx runtime stage"
    runtime_base = next(i for i in last if i.name == "FROM").args.split()[0]
    assert "nginx-unprivileged" in runtime_base, f"runtime stage is {runtime_base}"
    assert "node" not in runtime_base, "no Node runtime: the frontend is a static export (ADR 006)"


def test_every_base_image_is_pinned_to_a_patch_level_tag(instructions: list[Instruction]) -> None:
    external = []
    stage_names: set[str] = set()
    for i in instructions:
        if i.name != "FROM":
            continue
        words = [w for w in i.args.split() if not w.startswith("--")]
        if words[0] not in stage_names:
            external.append(words[0])
        if "as" in [w.lower() for w in words]:
            stage_names.add(words[-1])
    assert len(external) >= 2, f"expected a Node and an nginx base image, found {external}"
    for reference in external:
        assert ":" in reference, f"{reference} has no tag (that means latest)"
        tag = reference.split(":", 1)[1]
        assert tag != "latest", reference
        assert re.search(r"\d+\.\d+\.\d+", tag), f"{reference} is not pinned to a patch version"


def test_the_builder_installs_from_the_lockfile_before_copying_the_source(
    instructions: list[Instruction],
) -> None:
    builder = [i for i in instructions if i.stage == 0]
    runs = [i for i in builder if i.name == "RUN"]
    assert any("npm ci" in r.args for r in runs), "install with `npm ci` (exact lockfile)"
    assert not any("npm install" in r.args for r in runs), "`npm install` can change the lockfile"
    assert any("npm run build" in r.args for r in runs), "the real production build"

    def indices(name: str, needle: str) -> list[int]:
        return [n for n, i in enumerate(builder) if i.name == name and needle in i.args]

    manifests = indices("COPY", "package-lock.json")
    install = indices("RUN", "npm ci")
    assert manifests, "the manifests (package-lock.json) must be copied on their own first"
    assert install, "dependencies must be installed with `npm ci`"
    # Any COPY after the install brings the sources (next build type-checks tests/ and the config
    # files too, so this is `COPY . .`, made safe by .dockerignore).
    sources = [n for n, i in enumerate(builder) if i.name == "COPY" and n > install[0]]
    assert manifests[0] < install[0], "copy the manifests before installing"
    assert sources, "the sources must be copied after the dependencies are installed (caching)"


def test_the_final_stage_copies_only_the_built_site_and_the_nginx_config(
    last: list[Instruction],
) -> None:
    copies = [i for i in last if i.name in {"COPY", "ADD"}]
    assert copies, "the final stage copies nothing"
    from_builder = [i for i in copies if "--from=" in i.args]
    assert len(from_builder) == 1, "exactly one thing comes from the builder: the export"
    assert "/out" in from_builder[0].args, "the static export directory (out/)"
    assert "usr/share/nginx/html" in from_builder[0].args
    others = [i for i in copies if "--from=" not in i.args]
    assert len(others) == 1, [i.args for i in others]
    assert "nginx.conf" in others[0].args
    for i in copies:
        assert "node_modules" not in i.args, i.args
        assert ".env" not in i.args, i.args


def test_the_final_stage_never_switches_back_to_root(last: list[Instruction]) -> None:
    for i in last:
        if i.name == "USER":
            assert i.args.split(":")[0] not in {"root", "0"}, i.args


def test_the_image_declares_its_port_and_carries_its_own_healthcheck(
    last: list[Instruction],
) -> None:
    exposed = " ".join(i.args for i in last if i.name == "EXPOSE")
    assert "8080" in exposed, "nginx-unprivileged listens on 8080, not 80"
    checks = [i for i in last if i.name == "HEALTHCHECK"]
    assert len(checks) == 1
    assert "8080" in checks[0].args
    interval = re.search(r"--interval=(\d+)s", checks[0].args)
    assert interval is not None
    assert int(interval.group(1)) <= 30


def test_no_secret_can_be_baked_in_through_env_or_build_arguments(
    instructions: list[Instruction],
) -> None:
    risky = re.compile(r"PASSWORD|SECRET|TOKEN|KEY|CLIENT|DATABASE_URL", re.IGNORECASE)
    assert instructions, "no Dockerfile instructions"
    for i in instructions:
        if i.name in {"ENV", "ARG"}:
            names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", i.args) or [i.args.split()[0]]
            for name in names:
                assert not risky.search(name), f"{i.name} {name} could bake a secret into a layer"


def test_the_runtime_stage_installs_nothing(last: list[Instruction]) -> None:
    assert any(i.name == "COPY" for i in last), "no runtime stage yet"
    for i in last:
        if i.name == "RUN":
            for forbidden in ("npm ", "apk add", "apt-get", "curl "):
                assert forbidden not in i.args, f"runtime RUN uses {forbidden!r}"


# --- .dockerignore -----------------------------------------------------------------------


def test_dockerignore_keeps_secrets_dependencies_and_build_output_out_of_the_context() -> None:
    assert DOCKERIGNORE.is_file(), "frontend/.dockerignore does not exist"
    entries = {
        line.strip().rstrip("/")
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    required = {".env", ".env.*", "node_modules", ".next", "out", ".npm-cache", "coverage"}
    assert required <= entries, f"missing from .dockerignore: {sorted(required - entries)}"
    # The type check inside `next build` reads tests/ and the config files: they must stay in.
    assert "tests" not in entries, (
        "tests/ is part of the TypeScript project that `next build` checks"
    )


# --- nginx.conf: what it must say ---------------------------------------------------------


def test_nginx_listens_unprivileged_and_hides_its_version() -> None:
    lines = nginx_directives()
    assert "listen 8080;" in lines
    assert "server_tokens off;" in lines


def test_api_requests_are_proxied_to_the_api_service_unchanged() -> None:
    lines = nginx_directives()
    assert "location /api/ {" in lines
    # No URI after the host: with one, nginx would rewrite the path.
    assert "proxy_pass http://api:8000;" in lines
    # Without this nginx could rewrite a Location header; the browser must see the backend's own.
    assert "proxy_redirect off;" in lines


def test_nginx_never_hides_or_replaces_api_errors_with_the_site() -> None:
    joined = "\n".join(nginx_directives())
    assert not re.search(r"proxy_intercept_errors\s+on", joined), "would replace API error bodies"
    for line in nginx_directives():
        if line.startswith("try_files"):
            assert "/index.html" not in line, "an index.html fallback would mask real 404s"


def test_nginx_never_edits_the_headers_the_backend_relies_on() -> None:
    """The backend's Origin check and the session cookie must reach it untouched (ADR 012)."""
    for line in nginx_directives():
        lowered = line.lower()
        if lowered.startswith(("proxy_set_header", "proxy_hide_header", "proxy_ignore_headers")):
            assert "origin" not in lowered, line
            assert "cookie" not in lowered, line
            assert "location" not in lowered, line


def test_directory_redirects_are_relative_so_the_container_port_never_leaks() -> None:
    """/stocks -> /stocks/ must not become http://host:8080/stocks/ behind a published port."""
    assert "absolute_redirect off;" in nginx_directives()
