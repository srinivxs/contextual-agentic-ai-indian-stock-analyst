"""Which parts of the repository changed since the last green run (the owner, 2026-10-08).

Run by the pipeline's `changes` job, with the commit of the newest successful run on main:

    python3 .github/scripts/changed_parts.py <commit id, or "" when there is none>

It writes two answers to $GITHUB_OUTPUT, and the pipeline runs only what they need:

    backend=true|false    any file outside frontend/ changed: backend tests, image, rollout
    frontend=true|false   any file in frontend/ changed: publish the site

A page change used to wait 12 minutes for backend tests and a rollout it did not need; now a push
that changes only frontend/ publishes the site in a few minutes. The base is the last GREEN run,
not the previous push, so a change whose run failed is counted again by the next push. When in
doubt (no green run yet, a base this clone does not know, or nothing to compare, as in a re-run of
the same commit), everything counts as changed: the old, full path.

Only the standard library and `git` are used.
"""

import os
import re
import subprocess
import sys
from collections.abc import Mapping

FRONTEND = "frontend/"
COMMIT = re.compile(r"^[0-9a-f]{40}$")  # anything else (an option, a short id) never reaches git


def parts(paths: list[str]) -> dict[str, bool]:
    """Which parts the changed paths touch; an empty list means "could not tell": everything."""
    if not paths:
        return {"backend": True, "frontend": True}
    return {
        "backend": any(not path.startswith(FRONTEND) for path in paths),
        "frontend": any(path.startswith(FRONTEND) for path in paths),
    }


def changed_since(base: str) -> list[str]:
    """The files changed between ``base`` and this commit, or [] when that cannot be told."""
    if not COMMIT.match(base):
        return []
    done = subprocess.run(  # noqa: S603 - a fixed program with arguments, no shell
        ["git", "diff", "--name-only", base, "HEAD"],  # noqa: S607 - git from the runner's PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:  # a commit this clone does not have (history rewritten, say)
        return []
    return [line for line in done.stdout.splitlines() if line.strip()]


def main(argv: list[str], env: Mapping[str, str]) -> int:
    base = argv[1] if len(argv) > 1 else ""
    found = parts(changed_since(base))
    lines = "".join(f"{name}={'true' if value else 'false'}\n" for name, value in found.items())
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(lines)
    print(f"Changed since {base or 'nothing (no green run)'}: {lines.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv, os.environ))
