# Container tests

Tests for the Dockerfiles and the Compose stack (the `app` profile). They build and run real
containers, so they are **not** part of the normal `backend` pytest run and are never collected by it.

Run them explicitly from the repository root (Docker Desktop must be running; they fail, never skip):

```
backend\.venv\Scripts\python -m pytest docker/tests
```

## Isolation

- The image and single-container tests label everything `stock-analyst-p6test=1`, use random names and
  publish nothing on a host port.
- The stack tests run the real `docker-compose.yml` as a separate Compose project named
  `p6btest-<id>`, with its own containers, network and volume, random free ports on `127.0.0.1`, an
  env file of generated fake credentials (`--env-file`, so the real `.env` is never read), and the
  variables Compose uses removed from the inherited environment. They never touch `stock-analyst-*`.
- The database used is a throwaway PostgreSQL + pgvector container on a private network, with random
  passwords and the real role bootstrap. It cannot reach, or be reached from, the development database
  (`stock-analyst-db-1`), and they never read or write its volume.
- All credentials passed to containers are fake, and only at run time. If a run is killed half way, clean
  up with: `docker rm -f -v $(docker ps -aq --filter label=stock-analyst-p6test=1)` and, for stack
  tests, `docker rm -f -v $(docker ps -aq --filter name=p6btest-)`, then remove the networks and
  volumes whose names start with `p6btest-`.

## Files

| File | What it checks |
|---|---|
| `test_dockerfile_static.py` | What the Dockerfile and `.dockerignore` say (no Docker needed) |
| `test_image.py` | The built image: user, contents, size ceiling, layer caching |
| `test_runtime.py` | The api container under read-only / no-capabilities flags: health, shutdown, fail-fast |
| `test_migrate.py` | `alembic upgrade head` from the same image against a throwaway database |
| `test_frontend_static.py` | What `frontend/Dockerfile`, `.dockerignore` and `nginx.conf` say (no Docker needed) |
| `test_web_image.py` | The built web image: user, contents, size ceiling, install-layer caching |
| `test_compose_static.py` | The resolved Compose model: profiles, ports, who gets which credentials, hardening |
| `test_stack.py` | The whole `app` profile running, reached through nginx (Origin, cookies, JSON 404s, restart) |
| `test_stack_failures.py` | A failing migration, a missing secret, and the plain database-only command |
| `test_harness.py` | Proves the session-seeding helper against the real api (a failure elsewhere is not the harness) |
| `stack.py` | The isolated Compose project used by the stack tests |

`image-size-ceilings.json` is created from the first measured clean build, with a written justification.
