# 014 — Containers and the local Compose topology

- **Status:** Accepted (amends [ADR 006](006-nextjs-static-export.md), [ADR 008](008-minimal-aws-architecture.md),
  [ADR 011](011-database-access-and-migrations.md) and [ADR 013](013-follow-api-and-frontend-shell.md);
  each of those carries a dated entry under "Amendments")
- **Date:** 2026-09-21

## Context

Through P5 the API ran from a virtual environment and the frontend from `next dev`. P6 puts both in
containers and makes the whole application start from one command, so that (a) the images that P7 and P8
will ship exist, are small, run as non-root, and are proven to work, and (b) the browser can be shown one
origin serving pages and API, as it will be behind CloudFront (ADR 006, ADR 013 section 5).

P6 is local only. It does not touch AWS, ECR, ECS, TLS, Bedrock credentials, image scanning or signing,
and it does not add the worker (ADR 008: it arrives with the first job type, P9).

## Decision

### 1. The local topology

```
Browser ── http://localhost:3000 ──▶ web   nginx-unprivileged, listens on 8080 in the container
                                       ├─ /          the static export, baked into the image
                                       └─ /api/*  ─▶ api   uvicorn on 8000, NOT published to the host
                                                       │  runtime role (no DDL)
   migrate  one-off `alembic upgrade head`, admin role ─┴─▶ db   PostgreSQL 16 + pgvector
                                                              published on 127.0.0.1 only
```

| Local (Compose) | In AWS (ADR 008) | Notes |
|---|---|---|
| `web` (nginx) | CloudFront + S3 | A stand-in for behaviour, not the same software (see section 9) |
| `api` | ECS container `api` | The same image |
| `migrate` | The one-off migration ECS task | The same image, another `command:` |
| `db` | RDS PostgreSQL + pgvector | Local superuser versus `rds_superuser` (ADR 011) |
| (none) | ECS container `worker` | Added to Compose in P9 |

### 2. The backend image (`backend/Dockerfile`)

One image, several commands. The default command is the API; `migrate` runs `alembic upgrade head` from
the same image. There is no `ENTRYPOINT`, so `command:` can run any program in it, and P9's `worker` is
one more `command:`.

- Two stages: a builder installs the locked, production-only dependencies with `uv` (pinned to the
  version the lockfile was made with); the runtime stage copies the finished environment and the code and
  nothing else (no build tools, tests, dev dependencies or `.env`).
- `python:3.11.16-slim-bookworm`, pinned to a patch version. Numeric uid 10001. Code is owned by root
  and not writable by the running user. Bytecode is precompiled, because the runtime filesystem is
  read-only.
- The image `HEALTHCHECK` is **liveness only** (`/api/healthz`, standard-library Python, no `curl`). A
  database blip must not make a container look dead; readiness is `/api/readyz`.
- Exec-form `CMD`, so uvicorn is PID 1 and receives SIGTERM. No `--reload`, and no `--proxy-headers`:
  the backend never trusts `X-Forwarded-*` (ADR 012).
- Configuration and secrets are never in the image; they arrive as environment variables at start.
- Measured 228.7 MB unpacked (about 70 MB compressed); ceiling 275 MB.

The first time the image ran, it failed: `httpx` was a dev dependency that production code imports. It was
fixed on its own (`969f249`, "declare httpx as a runtime dependency"). That is what running the real image
is for.

### 3. The frontend image (`frontend/Dockerfile`)

A Node builder runs `npm ci` and the real `next build`; the runtime stage is
`nginxinc/nginx-unprivileged` holding only `out/` and `nginx.conf`. There is no Node, npm, node_modules
or source in the final image, and nothing configurable in it: the export calls relative `/api/v1/...`.

- `node:22.23.2-alpine3.24` and `nginxinc/nginx-unprivileged:1.31.6-alpine3.24`, pinned to a patch version.
- The manifests are copied first and installed with `npm ci`, so editing a page skips the install.
  The rest of the project is copied with `COPY . .`, guarded by `.dockerignore`, because `next build`
  type-checks `tests/` and the config files as well as `src/`.
- Runs as uid 101 on port 8080; its own `HEALTHCHECK`.
- Measured 59.7 MB unpacked (about 23 MB compressed); baseline 60 MB, ceiling 72 MB (baseline plus 20%).

### 4. `nginx.conf`: what it must and must not do

`/api/` is passed through untouched. Every rule below has a test (section 8) except the two marked
"no test", which are stated as intent only.

| Rule | Why |
|---|---|
| `proxy_pass http://api:8000;` with **no** path after the host | A path there makes nginx rewrite the request path |
| Never set, blank or hide `Origin`, `Cookie`, `Set-Cookie` or `Location` | The backend's Origin check (CSRF) and the session cookie depend on them (ADR 012) |
| `proxy_redirect off` | The browser must see the backend's own `Location` (the Google redirect) |
| No `proxy_intercept_errors` | The backend's JSON 404 and 401 must never become a page |
| `try_files $uri $uri/ =404`, with no fallback to `index.html` | An unknown page is a real 404, not the home page |
| `absolute_redirect off` | `/stocks` redirects to `/stocks/`; an absolute `Location` would carry the container's port 8080 |
| `server_tokens off` | No version banner |
| Long-lived cache only for `/_next/static/` (hashed names); `no-cache` for pages | A new build is picked up; the bundle is cacheable for good. Only the bundle's `immutable` header is tested; the pages' `no-cache` is not (no test) |
| No `X-Forwarded-*` | The backend ignores them by design (ADR 012), so setting them would only mislead (no test) |

### 5. The Compose `app` profile

`docker compose up -d --wait` starts **only the database**, exactly as before. The whole application is
`docker compose --profile app up --build --wait`.

`db` (healthy) → `migrate` (runs to completion) → `api` (healthy, by the image healthcheck) → `web`.
A failing migration therefore leaves `api` and `web` created but never started.

- Compose builds `api` and `migrate` from the same `build:` definition. **None of the three services it
  builds (`api`, `migrate`, `web`) names an `image:`** (only `db`, which is pulled, does), so Compose tags
  what it builds with the project name, and a second project (the container tests) can never overwrite
  an image the developer's own stack uses.
- `web` is published on `127.0.0.1:${WEB_PORT:-3000}`. 3000 is the port `npm run dev` uses and the one
  already registered as a Google redirect URI, so the two cannot run at the same time.
- `PUBLIC_BASE_URL` for the api is **derived** as `http://localhost:${WEB_PORT:-3000}`: it is what the
  browser sends as `Origin`. The `PUBLIC_BASE_URL` in `.env` is for tools run on the host and is ignored
  by the containers, as are `DATABASE_URL` and the other host values there.
- The api runs with `APP_ENV=local` and `COOKIE_SECURE=false`, because plain-HTTP localhost cannot store
  a Secure cookie.

### 6. Credentials and configuration

- **Only `migrate` receives admin credentials** (`MIGRATION_DATABASE_URL`). The api gets the runtime
  role's `DATABASE_URL`. This is ADR 011's rule made real: the long-running containers never hold DDL
  rights.
- **No `env_file` anywhere.** Each container lists exactly the variables it needs, so nothing reaches a
  container by accident. `web` receives no environment at all.
- **`${VAR:?}` only for the two database passwords; `${VAR:-}` for the Google and session settings.**
  Compose enforces `:?` even for services in an inactive profile, so using it for the app-only secrets
  would break the plain database-only command for anyone without Google settings. The price is that a
  blank Google or session setting is refused when the api starts (the process exits and names only the
  field), not when Compose loads the file.
- Secrets are never in an image, a build argument, an image layer or the build context (`.dockerignore`).

### 7. Hardening

`api` and `web` run with a read-only root filesystem, a `tmpfs` at `/tmp` as the only writable place,
`cap_drop: [ALL]` and `no-new-privileges`. Both images run as a non-root numeric user. The api is never
published; the database and web are published on `127.0.0.1` only. `restart:` is not set: a crashed
container stays down, which is what you want when developing.

### 8. Tests, isolation and the invariant → test map

The container tests are in `docker/tests/` with their own `pytest.ini`, and are **not** part of the
backend suite: they build and run real containers (about 3 to 4 minutes) and fail, never skip, when
Docker is not available. Run from the repository root:
`backend\.venv\Scripts\python -m pytest docker/tests`.

Isolation: the stack tests run the real `docker-compose.yml` as a separate Compose project
`p6btest-<id>`, with its own containers, network and volume, random free ports on `127.0.0.1`, an env
file of generated fake credentials (`--env-file`, so the real `.env` is never read), and every variable
Compose uses removed from the inherited environment. A session is created by writing its row straight
into the test database. Nothing named `stock-analyst-*` is touched.

Only what the tests actually prove is claimed. Paths are under `docker/tests/`.

| Guarantee | Proven by |
|---|---|
| Plain `docker compose up -d --wait` starts only the database, and needs no app settings | `test_compose_static.py::test_without_the_profile_only_the_database_is_defined`, `test_database_only_use_needs_only_the_database_passwords`; `test_stack_failures.py::test_the_plain_database_command_still_works_with_no_app_settings_at_all` |
| A missing database password stops Compose and names only the variable | `test_compose_static.py::test_a_missing_database_password_stops_compose_and_names_only_the_variable` |
| A missing Google setting stops the api at start-up, naming only the field; `web` never starts | `test_stack_failures.py::test_a_missing_google_secret_stops_the_api_naming_only_the_setting`; `test_runtime.py::test_a_missing_required_setting_stops_it_at_startup_naming_only_the_field` |
| Start order; a failing migration blocks `api` and `web` | `test_compose_static.py::test_startup_order_is_db_then_migrate_then_api_then_web`; `test_stack_failures.py::test_a_failing_migration_stops_the_api_and_web_from_ever_starting`; `test_stack.py::test_everything_comes_up_healthy_and_migrate_ran_once_to_completion` |
| `migrate` works from an empty database, is repeatable, and refuses without its URL or with a wrong password without echoing one | `test_migrate.py::test_migrate_brings_an_empty_database_to_the_newest_revision`, `test_running_it_again_changes_nothing`, `test_a_wrong_admin_password_fails_the_migration_without_echoing_any_password`, `test_without_the_migration_url_it_refuses_and_names_only_the_variable` |
| Only `migrate` holds admin credentials; the api runs as the runtime role | `test_compose_static.py::test_only_migrate_holds_the_admin_credentials`, `test_the_api_gets_the_runtime_role_and_exactly_the_settings_it_needs`; `test_migrate.py::test_the_api_started_with_the_runtime_role_is_ready_and_never_holds_admin_credentials`; `test_stack.py::test_the_running_api_is_hardened_non_root_and_has_no_admin_credentials` |
| No `env_file`; `web` gets no configuration or secrets | `test_compose_static.py::test_no_service_uses_env_file_so_nothing_reaches_a_container_by_accident`, `test_the_web_container_receives_no_configuration_or_secrets`; `test_stack.py::test_the_web_container_runs_as_a_non_root_user_with_no_secrets` |
| The api is not reachable from the host; only `db` and `web` are published, on loopback | `test_compose_static.py::test_only_the_database_and_web_publish_ports_and_only_on_loopback`; `test_stack.py::test_the_api_is_reachable_only_through_nginx`, `test_every_published_port_is_bound_to_loopback_only` |
| Read-only filesystem, only `/tmp` writable, no capabilities, no new privileges | `test_compose_static.py::test_the_long_running_containers_have_a_read_only_filesystem_and_no_capabilities`; `test_stack.py::test_the_running_api_is_hardened_non_root_and_has_no_admin_credentials`, `test_the_running_web_container_is_hardened_like_the_api`; `test_runtime.py::test_the_root_filesystem_is_really_read_only_and_only_tmp_is_writable` |
| Non-root numeric users | `test_dockerfile_static.py::test_the_runtime_stage_runs_as_a_numeric_non_root_user`; `test_image.py::test_it_runs_as_a_numeric_non_root_user`; `test_web_image.py::test_it_runs_as_a_non_root_user`; `test_frontend_static.py::test_the_final_stage_never_switches_back_to_root` |
| No secret in an image, its history, its build arguments or the build context; real local secrets cannot reach a test stack | `test_image.py::test_no_secret_is_recorded_in_the_image_history`; `test_web_image.py::test_no_secret_is_recorded_in_the_image_history`; `test_dockerfile_static.py::test_no_secret_can_be_baked_in_through_env_or_build_arguments`; `test_frontend_static.py::test_no_secret_can_be_baked_in_through_env_or_build_arguments`, `test_dockerignore_keeps_secrets_dependencies_and_build_output_out_of_the_context`; `test_compose_static.py::test_the_real_local_secrets_can_never_reach_a_test_stack` |
| Base images are pinned to a patch version; multi-stage; no installs in the runtime stage | `test_dockerfile_static.py::test_every_base_image_is_pinned_to_a_patch_level_tag`, `test_it_is_a_multi_stage_build`, `test_the_runtime_stage_installs_nothing`; `test_frontend_static.py::test_every_base_image_is_pinned_to_a_patch_level_tag`, `test_it_is_a_multi_stage_build_and_the_final_stage_is_nginx_not_node`, `test_the_runtime_stage_installs_nothing` |
| The web image holds only the built site; it cannot be modified by the user running nginx | `test_web_image.py::test_it_contains_the_built_site_and_nothing_it_should_not`, `test_the_site_files_cannot_be_modified_by_the_user_running_nginx`; `test_frontend_static.py::test_the_final_stage_copies_only_the_built_site_and_the_nginx_config` |
| Image sizes stay within their recorded ceilings; a source change reuses the dependency layers | `test_image.py::test_the_image_stays_within_its_documented_size_ceiling`, `test_a_source_change_reuses_the_dependency_layers`; `test_web_image.py::test_the_image_stays_within_its_documented_size_ceiling`, `test_a_source_change_reuses_the_cached_dependency_install` |
| `Origin` reaches the backend through nginx (a foreign origin is refused and nothing is written) | `test_stack.py::test_a_foreign_origin_is_refused_by_the_backend_through_nginx`; `test_frontend_static.py::test_nginx_never_edits_the_headers_the_backend_relies_on` |
| The backend's own JSON errors reach the browser: an unknown `/api` path is a JSON 404, an unauthenticated call a 401 that stays `no-store` | `test_stack.py::test_an_unknown_api_path_is_the_backends_json_404_not_an_html_page`, `test_unauthenticated_requests_get_the_backends_401_and_keep_no_store`; `test_frontend_static.py::test_nginx_never_hides_or_replaces_api_errors_with_the_site` |
| The path is passed through unchanged | `test_frontend_static.py::test_api_requests_are_proxied_to_the_api_service_unchanged`; `test_stack.py::test_the_api_is_reachable_only_through_nginx` |
| The login redirect and its cookie arrive intact, with the web origin as `redirect_uri` | `test_stack.py::test_the_login_redirect_and_its_cookie_arrive_intact`; `test_compose_static.py::test_the_api_gets_the_runtime_role_and_exactly_the_settings_it_needs` |
| Pages: unknown page is a real 404; relative directory redirect; no version banner; bundle cacheable, pages not | `test_stack.py::test_an_unknown_page_is_a_real_404_not_the_home_page`, `test_a_directory_redirect_is_relative_so_the_container_port_never_leaks`, `test_the_server_does_not_announce_its_version`, `test_the_pages_are_served_and_the_bundle_is_cacheable_for_good`; `test_frontend_static.py::test_nginx_listens_unprivileged_and_hides_its_version`, `test_directory_redirects_are_relative_so_the_container_port_never_leaks` |
| A follow survives a refresh and an api restart | `test_stack.py::test_follow_survives_a_refresh_and_an_api_restart` |
| Liveness needs no database but readiness does; graceful stop; JSON logs without secrets | `test_runtime.py::test_liveness_needs_no_database_but_readiness_does`, `test_stopping_it_is_graceful_and_quick`, `test_logs_are_json_on_stdout_and_carry_no_secrets` |
| A test stack cannot collide with the developer's: no fixed image or container names; one build definition for `api` and `migrate` | `test_compose_static.py::test_no_service_pins_a_container_name`, `test_api_and_migrate_are_built_from_the_same_backend_image_definition` |
| The session-seeding helper the stack tests rely on is accepted by the real api | `test_harness.py::test_a_directly_seeded_session_is_accepted_by_the_real_api` |

**Evidence.** The container suite is 91 collected items (34 from P6a, 57 from P6b) and passes. The tests
were written first and shown red for the right reason where they could be. The running-stack tests
could only fail at their fixture before the stack existed, so their assertions were validated
afterwards by deliberate breakage: **25 of 25** breakages in P6a and **55 of 55** in P6b (28 checked by
the static tests, 27 also by the running stack) were caught, each by the test named for that property
and not by a blanket failure. Sources were restored byte for byte after each. In P6c, with no code
changed, eight of them were run again against the final tree as a regression check of the invariants
above (admin credentials given to `api`, `api` published, `web` on `0.0.0.0`, `api` not waiting for
`migrate`, `api` losing `read_only`, an API 404 turned into a page, `Origin` blanked, and a root
runtime stage): **8 of 8** caught, files restored byte for byte. Two of the P6b tests
were corrected while implementing, to match real Compose behaviour (Compose prints no `image` for a
build-only service, and blocked dependents sit in `created` rather than being absent); neither
assertion was weakened.

### 9. What local parity proves, and what it does not

The `web` container proves that the application works behind **one origin**: pages and API together,
the Origin check, the session cookie, the login redirect, errors passing through untouched. It does not
prove production. nginx is not CloudFront: the CloudFront Function that rewrites extension-less URLs, the
caching rules for `/api/*`, cookie, query-string and `Origin` forwarding, and the ALB hop all belong to
P7 and P8. The stack runs over plain HTTP with `APP_ENV=local`: the `__Host-session` cookie, `Secure`,
HTTPS and the production-mode settings are exercised only by unit tests until then.

## What changed from the plan

The roadmap and the approved design said one thing; this is what exists. Nothing here is hidden.

| Plan | What exists |
|---|---|
| Roadmap: one image with `api` and `worker` commands | One image with the default `api` command plus `migrate`. The worker arrives with P9 |
| Roadmap: "`docker compose up` gives working login and follow" | `docker compose --profile app up --build --wait` does. Plain `up` is deliberately database-only |
| Approved design: required secrets via `${VAR:?}` | `${VAR:-}` for the Google and session settings, because Compose checks `:?` even for inactive profiles (section 6) |
| Approved design hardened the `api` | `web` is hardened the same way |
| Web published on `127.0.0.1:3000` | Published on `127.0.0.1:${WEB_PORT:-3000}`; the api's `PUBLIC_BASE_URL` is derived from it |
| P6a tests: 34 items | 91 items in the container suite; 25 plus 55 breakages |
| `docs/mvp.md` item 15: "`docker compose up` runs the whole stack locally" | Reworded in P6c to `--profile app`, with the worker joining at P9 |
| ADR 004: cost estimate "to be re-verified in P6" | Already moved to P7 by ADR 008 (the itemised cost table is owed before any provisioning). ADR 004 is left untouched |
| `the project notes` after P6a | It was not updated for P6a and still listed the Dockerfiles as unbuilt. Corrected in P6c |

## Alternatives considered

| Option | Why not |
|---|---|
| Run `docker run` commands from a script | Compose gives ordering, health gating, one network and one command, and is what the roadmap asked for |
| Next.js `standalone` server in a Node container | A running Node server for files that are already static (ADR 006). A second runtime to secure and explain |
| Caddy or Traefik as the local proxy | Another tool to learn. nginx is what most readers already know, and the config is short |
| `env_file: .env` for the api | Passes every variable in the file, including the admin credentials, to a container that must not have them |
| `${VAR:?}` for every required secret | Breaks the database-only command, because Compose checks inactive profiles (section 6) |
| One Dockerfile for both images | Different bases, users and build tools; the backend needs neither Node nor nginx, the frontend neither Python nor `uv` |
| A `web` health check that also checks the api | The api's own `HEALTHCHECK` already gates `web`; coupling them would make `web` restart because of a database blip |
| No web container (keep `npm run dev`) | It cannot show one origin built from the same files that production will serve |

## Tradeoffs and known limits

- **nginx resolves `api` once, when it starts.** A `restart` of the api container keeps its address and
  is tested. If only the api container is *recreated* and gets a new address, `web` keeps pointing at the
  old one and returns 502 until it is restarted too (`docker compose --profile app restart web`).
  Handling it (a resolver with a variable) was deliberately left out of P6; P7 and P8 can revisit it.
- **Local parity is behavioural, not identical** (section 9).
- **Google sign-in through the stack is not automated.** The automated tests seed a session and check the
  login redirect and its cookie, but not the callback through nginx. The manual run below is the
  evidence for that part.
- **Port 3000 is shared** with `npm run dev`. Changing `WEB_PORT` means registering a new redirect URI on
  the Google client.
- **Images are pinned by tag, not digest**, and are not scanned, signed, given an SBOM or built for more
  than this machine's architecture. Digest pinning, scanning and signing are P8. P7 and P8 must confirm
  the Fargate CPU architecture matches the image.
- **A blank Google or session setting fails when the api starts**, not when Compose loads (section 6).
- **No security headers, CSP or rate limiting** were added. They stay deferred to P16 (ADR 012).
- **The breakage harness is not in the repository.** The results are recorded above as counts; running
  them again needs the script. Adding it would be new tooling, so it was left out.
- **`docker compose down -v` deletes the development database.** Plain `down` keeps it.

## Consequences for other decisions

- ADR 006: the frontend Dockerfile is real and is a local stand-in only. Nothing about production changes.
- ADR 008: `api` and `migrate` exist as containers; `worker` is P9. The Compose topology is not the ECS
  topology.
- ADR 011: the "runtime containers never hold DDL rights" rule is now enforced and tested in Compose.
- ADR 012: the Origin check and the session cookie are exercised through a real reverse proxy.
- ADR 013: the "production shape" limit is narrowed to CloudFront and HTTPS (P7 and P8).
- P7 inherits: CloudFront forwarding for `/api/*`, the Fargate CPU architecture, and the itemised cost
  table. P8 inherits: digest pinning, scanning, signing and the CI trigger question in `the project notes`.
  P9 adds the `worker` service to Compose.

## Verified by hand

On 2026-09-21 the owner completed all twelve steps of the manual check successfully, running the whole
stack with `docker compose --profile app up --build --wait` and a real Google account. Only this status
summary is recorded; no cookie, token, authorization code, email address or `.env` value was shared or
is kept here.

| Step | Result |
|---|---|
| The `app` stack builds and starts (`db`, `api`, `web` healthy; `migrate` exited cleanly) | Passed |
| The containerized frontend opens on `http://localhost:3000` | Passed |
| Google sign-in through the containerized stack, landing on the Stocks page | Passed |
| A followed stock is still followed after a browser refresh | Passed |
| Signed in and followed after `docker compose --profile app restart api` | Passed |
| Signed in and followed after `docker compose --profile app down` and `up` (no `-v`) | Passed |
| Sign-out, then sign in again with the follow still present | Passed |
| `docker compose up -d --wait` afterwards starts only the database | Passed |
| The host backend and `npm run dev` still work against the same database | Passed |
| No CORS problems observed | Passed |

This manual run is the evidence for Google sign-in through nginx. The automated container tests do not
drive the OAuth callback through the stack.
