"""The `app` profile running: db -> migrate -> api -> web, reached the way a browser reaches it.

Everything goes through nginx on the published loopback port (the local stand-in for CloudFront),
and the session is created by writing its row straight into the test database, so Google is not
involved. One stack is built and started for the whole module (about two minutes).
"""

import re
import subprocess

import httpx

from helpers import wait_for
from stack import Stack, seed_session


def envelope(response: httpx.Response) -> dict[str, object]:
    """The backend's one error shape: {error: {code, message, request_id}}."""
    body = response.json()
    assert isinstance(body, dict), body
    error = body.get("error")
    assert isinstance(error, dict), body
    return error


# --- the stack comes up in order -----------------------------------------------------------


def test_everything_comes_up_healthy_and_migrate_ran_once_to_completion(stack: Stack) -> None:
    for name in ("db", "api", "web"):
        assert stack.inspect(name, "{{json .State.Health.Status}}") == "healthy", name
    assert stack.inspect("migrate", "{{json .State.Status}}") == "exited"
    assert stack.inspect("migrate", "{{json .State.ExitCode}}") == 0
    assert stack.psql("select count(*) from stocks") == "3"


# --- the static site -----------------------------------------------------------------------


def test_the_pages_are_served_and_the_bundle_is_cacheable_for_good(stack: Stack) -> None:
    with stack.client() as browser:
        home = browser.get("/")
        assert home.status_code == 200
        assert home.headers["content-type"].startswith("text/html")
        assert browser.get("/stocks/").status_code == 200

        asset = re.search(r'/_next/static/[^"\']+\.js', home.text)
        assert asset, "the home page references no bundled script"
        script = browser.get(asset.group(0))
        assert script.status_code == 200
        assert "immutable" in script.headers.get("cache-control", "")


def test_a_directory_redirect_is_relative_so_the_container_port_never_leaks(stack: Stack) -> None:
    with stack.client() as browser:
        response = browser.get("/stocks")
    assert response.status_code in (301, 308)
    assert response.headers["location"] == "/stocks/"


def test_an_unknown_page_is_a_real_404_not_the_home_page(stack: Stack) -> None:
    with stack.client() as browser:
        response = browser.get("/no-such-page/")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")


def test_the_server_does_not_announce_its_version(stack: Stack) -> None:
    with stack.client() as browser:
        server = browser.get("/").headers.get("server", "")
    assert not re.search(r"\d", server), f"Server header reveals a version: {server!r}"


# --- /api goes to the backend, untouched ---------------------------------------------------


def test_the_api_is_reachable_only_through_nginx(stack: Stack) -> None:
    with stack.client() as browser:
        assert browser.get("/api/healthz").status_code == 200
    ports = stack.inspect("api", "{{json .NetworkSettings.Ports}}")
    assert isinstance(ports, dict)
    published = [b for bindings in ports.values() for b in (bindings or [])]
    assert published == [], "the api has a published port"


def test_an_unknown_api_path_is_the_backends_json_404_not_an_html_page(stack: Stack) -> None:
    with stack.client() as browser:
        response = browser.get("/api/no-such-thing")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert envelope(response)["code"] == "not_found"


def test_unauthenticated_requests_get_the_backends_401_and_keep_no_store(stack: Stack) -> None:
    with stack.client() as browser:
        response = browser.get("/api/v1/me")
    assert response.status_code == 401
    assert envelope(response)["code"] == "unauthorized"
    assert response.headers.get("cache-control") == "no-store"


def test_the_login_redirect_and_its_cookie_arrive_intact(stack: Stack) -> None:
    with stack.client() as browser:
        response = browser.get("/api/v1/auth/google/login")
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/")
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("oauth_login=")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert stack.env["GOOGLE_CLIENT_ID"] in response.headers["location"]
    assert stack.google_secret not in response.headers["location"]
    redirect_uri = (
        f"http%3a%2f%2flocalhost%3a{stack.web_port}%2fapi%2fv1%2fauth%2fgoogle%2fcallback"
    )
    assert redirect_uri in response.headers["location"].lower(), (
        "redirect_uri is not the web origin"
    )


# --- a signed-in user's flow, end to end ---------------------------------------------------


def test_follow_survives_a_refresh_and_an_api_restart(stack: Stack) -> None:
    token = seed_session(stack.psql, "p6b-follow@example.test")
    with stack.client(token) as browser:
        me = browser.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["email"] == "p6b-follow@example.test"

        followed = browser.put("/api/v1/stocks/RELIANCE/follow", headers={"Origin": stack.origin})
        assert followed.status_code == 204

        # "Refresh": a brand new request with nothing but the cookie.
        listing = browser.get("/api/v1/stocks").json()["items"]
        assert {s["symbol"]: s["followed"] for s in listing}["RELIANCE"] is True

    stack.compose("restart", "api")
    stack.wait_healthy("api")
    wait_for(lambda: _stocks_status(stack, token) == 200, "the api to answer through nginx again")

    with stack.client(token) as browser:
        after = browser.get("/api/v1/stocks").json()["items"]
        assert {s["symbol"]: s["followed"] for s in after}["RELIANCE"] is True, "follow was lost"
        unfollowed = browser.delete(
            "/api/v1/stocks/RELIANCE/follow", headers={"Origin": stack.origin}
        )
        assert unfollowed.status_code == 204
        final = browser.get("/api/v1/stocks").json()["items"]
        assert {s["symbol"]: s["followed"] for s in final}["RELIANCE"] is False


def _stocks_status(stack: Stack, token: str) -> int:
    try:
        with stack.client(token) as browser:
            return browser.get("/api/v1/stocks").status_code
    except httpx.HTTPError:
        return 0


def test_a_foreign_origin_is_refused_by_the_backend_through_nginx(stack: Stack) -> None:
    """If nginx dropped or rewrote Origin, this would succeed and the CSRF guard would be void."""
    token = seed_session(stack.psql, "p6b-csrf@example.test")
    with stack.client(token) as browser:
        response = browser.put(
            "/api/v1/stocks/TCS/follow", headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 403
        assert envelope(response)["code"] == "forbidden"
        listing = browser.get("/api/v1/stocks").json()["items"]
        assert {s["symbol"]: s["followed"] for s in listing}["TCS"] is False, "the write happened"


# --- what is actually running is locked down ------------------------------------------------


def test_the_running_api_is_hardened_non_root_and_has_no_admin_credentials(stack: Stack) -> None:
    assert stack.inspect("api", "{{json .HostConfig.ReadonlyRootfs}}") is True
    assert stack.inspect("api", "{{json .HostConfig.CapDrop}}") == ["ALL"]
    assert "no-new-privileges:true" in stack.inspect("api", "{{json .HostConfig.SecurityOpt}}")
    assert stack.inspect("api", "{{json .Config.User}}") == "10001"

    environment = "\n".join(stack.inspect("api", "{{json .Config.Env}}"))
    assert "MIGRATION_DATABASE_URL" not in environment
    assert stack.admin_password not in environment
    assert stack.admin_user not in environment


def test_the_running_web_container_is_hardened_like_the_api(stack: Stack) -> None:
    assert stack.inspect("web", "{{json .HostConfig.ReadonlyRootfs}}") is True
    assert stack.inspect("web", "{{json .HostConfig.CapDrop}}") == ["ALL"]
    assert "no-new-privileges:true" in stack.inspect("web", "{{json .HostConfig.SecurityOpt}}")
    assert stack.inspect("web", "{{json .HostConfig.Tmpfs}}").keys() == {"/tmp"}

    container = stack.container("web")
    assert container
    write_site = subprocess.run(
        ["docker", "exec", container, "sh", "-c", "touch /usr/share/nginx/html/nope"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    write_tmp = subprocess.run(
        ["docker", "exec", container, "sh", "-c", "touch /tmp/ok"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert write_site.returncode != 0, "the site directory is writable"
    assert write_tmp.returncode == 0, "/tmp is not writable, nginx cannot run"


def test_the_web_container_runs_as_a_non_root_user_with_no_secrets(stack: Stack) -> None:
    container = stack.container("web")
    assert container
    uid = subprocess.run(
        ["docker", "exec", container, "id", "-u"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    assert uid.isdigit()
    assert int(uid) != 0

    environment = "\n".join(stack.inspect("web", "{{json .Config.Env}}"))
    for secret in (
        stack.google_secret,
        stack.session_secret,
        stack.app_password,
        stack.admin_password,
    ):
        assert secret not in environment


def test_every_published_port_is_bound_to_loopback_only(stack: Stack) -> None:
    for name in ("db", "web"):
        ports = stack.inspect(name, "{{json .NetworkSettings.Ports}}")
        assert isinstance(ports, dict)
        published = [b for bindings in ports.values() for b in (bindings or [])]
        assert published, f"{name} publishes nothing"
        assert {b["HostIp"] for b in published} == {"127.0.0.1"}, (name, published)
