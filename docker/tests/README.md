# Container tests

Tests for the Dockerfiles and Compose stack. They build and run real containers, so they are **not** part
of the normal `backend` pytest run and are never collected by it.

Run them explicitly from the repository root (Docker Desktop must be running; they fail, never skip):

```
backend\.venv\Scripts\python -m pytest docker/tests
```

## Isolation

- Every container, network and image these tests create is labelled `stock-analyst-p6test=1`, uses a
  random name, and is removed afterwards. Nothing they create is published on a host port.
- The database used is a throwaway PostgreSQL + pgvector container on a private network, with random
  passwords and the real role bootstrap. It cannot reach, or be reached from, the development database
  (`stock-analyst-db-1`), and they never read or write its volume.
- All credentials passed to containers are fake, and only at run time. If a run is killed half way, clean
  up with: `docker rm -f -v $(docker ps -aq --filter label=stock-analyst-p6test=1)`.

## Files

| File | What it checks |
|---|---|
| `test_dockerfile_static.py` | What the Dockerfile and `.dockerignore` say (no Docker needed) |
| `test_image.py` | The built image: user, contents, size ceiling, layer caching |
| `test_runtime.py` | The api container under read-only / no-capabilities flags: health, shutdown, fail-fast |
| `test_migrate.py` | `alembic upgrade head` from the same image against a throwaway database |

`image-size-ceilings.json` is created from the first measured clean build, with a written justification.
