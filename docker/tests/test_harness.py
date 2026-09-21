"""Proves the P6b test harness itself, so a later failure means the stack, not the tests.

`seed_session` writes a session row straight into the database. This checks that the real api,
given the runtime role, accepts that cookie. It runs against the already-existing api image and a
throwaway database, so it passes today.
"""

from conftest import ThrowawayDatabase
from helpers import FAKE_ENV, docker, running_api, wait_healthy
from stack import seed_session
from test_migrate import migrate


def get_me(container: str, cookie: str | None) -> tuple[int, str]:
    headers = {"Cookie": f"session={cookie}"} if cookie else {}
    script = (
        "import urllib.request as u, urllib.error as e\n"
        f"r = u.Request('http://127.0.0.1:8000/api/v1/me', headers={headers!r})\n"
        "try:\n"
        "    x = u.urlopen(r, timeout=3); print(x.status, x.read().decode())\n"
        "except e.HTTPError as x:\n"
        "    print(x.code, x.read().decode())\n"
    )
    out = docker("exec", container, "python", "-c", script).stdout.strip()
    code, _, body = out.partition(" ")
    return int(code), body


def test_a_directly_seeded_session_is_accepted_by_the_real_api(
    image: str, database: ThrowawayDatabase
) -> None:
    assert migrate(image, database, database.admin_url).returncode == 0
    token = seed_session(database.query, "p6b-harness@example.test")
    env = {**FAKE_ENV, "DATABASE_URL": database.app_url}

    with running_api(image, env, network=database.network) as name:
        wait_healthy(name)
        status, body = get_me(name, token)
        assert status == 200, body
        assert "p6b-harness@example.test" in body
        assert get_me(name, "not-the-token")[0] == 401
        assert get_me(name, None)[0] == 401
