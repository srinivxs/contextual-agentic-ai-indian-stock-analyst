"""Plain helpers for the container tests (fixtures live in conftest.py).

Everything here that creates something in Docker labels it, so a crashed run can be cleaned up with
`docker rm -f $(docker ps -aq --filter label=stock-analyst-p6test=1)`. Nothing here ever touches a
container, network or volume it did not create itself, so the normal development database is safe.
"""

import json
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
INIT_SCRIPTS = REPO / "docker" / "postgres-init"
RUN_ID = uuid.uuid4().hex[:8]
LABEL = "stock-analyst-p6test=1"

# Fake, and only ever passed at run time: none of these may appear in an image or in any output.
FAKE_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://nobody:not-a-real-password@db.invalid:5432/nothing",
    "PUBLIC_BASE_URL": "http://localhost:3000",
    "GOOGLE_CLIENT_ID": "1234567890-p6fake.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "GOCSPX-p6-fake-client-secret-never-real",
    "SESSION_SECRET": "p6-fake-session-secret-at-least-32-characters-long",
}
FAKE_SECRET_VALUES = [
    "not-a-real-password",
    FAKE_ENV["GOOGLE_CLIENT_SECRET"],
    FAKE_ENV["SESSION_SECRET"],
]


def docker(*args: str, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-3000:]
        raise AssertionError(
            f"`docker {' '.join(args[:2])} ...` failed ({proc.returncode}):\n{tail}"
        )
    return proc


def output(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout + proc.stderr


def wait_for(check: Callable[[], bool], what: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1.0)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")


# --- reading a Dockerfile ----------------------------------------------------------------


@dataclass(frozen=True)
class Instruction:
    name: str  # upper-case: FROM, RUN, COPY, USER, CMD ...
    args: str
    stage: int  # 0 for the first FROM's stage, and so on


def parse_dockerfile(text: str) -> list[Instruction]:
    """Instructions in order, with comments dropped and backslash continuations joined."""
    logical: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.startswith("#"):  # a comment in the middle of a continued instruction
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        logical.append(pending + line)
        pending = ""
    if pending:
        logical.append(pending.strip())

    instructions: list[Instruction] = []
    stage = -1
    for line in logical:
        name, _, args = line.partition(" ")
        name = name.upper()
        if name == "FROM":
            stage += 1
        instructions.append(Instruction(name, args.strip(), max(stage, 0)))
    return instructions


def final_stage(instructions: list[Instruction]) -> list[Instruction]:
    last = max((i.stage for i in instructions), default=0)
    return [i for i in instructions if i.stage == last]


# --- running things ----------------------------------------------------------------------


def container_name(kind: str) -> str:
    return f"p6a-{RUN_ID}-{kind}-{uuid.uuid4().hex[:6]}"


def local_secret_values() -> dict[str, str]:
    """The developer's real secrets, if a `.env` exists (only to prove they are not in images)."""
    found: dict[str, str] = {}
    for name in (".env", ".env.migration"):
        path = REPO / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            wanted = re.search(r"PASSWORD|SECRET|CLIENT_ID|DATABASE_URL", key)
            if sep and not line.startswith("#") and len(value) >= 8 and wanted:
                found[key] = value.strip()
    return found


# Flags Compose will apply to the api in P6b. The image must work under all of them.
HARDENING = [
    "--read-only",
    "--tmpfs",
    "/tmp",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
]


@contextmanager
def running_api(
    image: str,
    env: dict[str, str] | None = None,
    *,
    network: str | None = None,
    hardened: bool = True,
) -> Iterator[str]:
    """Start the image's default command in the background; always remove it afterwards."""
    name = container_name("api")
    args = ["run", "-d", "--name", name, "--label", LABEL]
    if hardened:
        args += HARDENING
    if network:
        args += ["--network", network]
    for key, value in (FAKE_ENV if env is None else env).items():
        args += ["-e", f"{key}={value}"]
    docker(*args, image)
    try:
        yield name
    finally:
        docker("rm", "-f", "-v", name, check=False)


def health(name: str) -> str:
    result = docker("inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{end}}", name)
    return result.stdout.strip()


def wait_healthy(name: str, timeout: float = 90.0) -> None:
    def ready() -> bool:
        state = docker("inspect", "-f", "{{.State.Status}}", name).stdout.strip()
        if state != "running":
            raise AssertionError(f"container {state} before it became healthy:\n{logs(name)}")
        return health(name) == "healthy"

    wait_for(ready, "the container to become healthy", timeout)


def logs(name: str) -> str:
    return output(docker("logs", name, check=False))


def http_status(name: str, path: str, port: int = 8000) -> int:
    """GET a path from inside the container (nothing is published) and return the status code."""
    script = (
        "import urllib.request as u, urllib.error as e\n"
        "try:\n"
        f"    print(u.urlopen('http://127.0.0.1:{port}{path}', timeout=3).status)\n"
        "except e.HTTPError as x:\n"
        "    print(x.code)\n"
    )
    return int(docker("exec", name, "python", "-c", script).stdout.strip())


def inspect_json(target: str, template: str) -> object:
    return json.loads(docker("inspect", "-f", template, target).stdout)
