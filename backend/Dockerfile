# The backend image: one image, several commands (ADR 008).
#
#   default command  the API (uvicorn)
#   `alembic upgrade head`  the migration, run as a one-off task with the admin URL
#   (the worker command arrives with the first job type, P9)
#
# Two stages. The BUILDER installs the locked, production-only dependencies. The RUNTIME stage copies
# the finished virtual environment and our code and nothing else: no build tools, no package
# caches, no tests, no dev dependencies, no .env. Configuration and secrets are never part of the
# image; they arrive as environment variables when a container starts.

# ---- builder ----------------------------------------------------------------------------------
FROM python:3.11.16-slim-bookworm AS builder

# uv installs exactly what uv.lock says. Pinned to the version the lockfile was made with.
# UV_COMPILE_BYTECODE: precompile the .pyc files now, because the runtime filesystem is read-only.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install uv==0.12.17

# Only the dependency manifests first. This layer is rebuilt only when the dependencies change,
# not when application code does.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- runtime ----------------------------------------------------------------------------------
FROM python:3.11.16-slim-bookworm AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# A fixed numeric uid, so it can be named in orchestrator settings and compared with a file owner.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/blobs && chown 10001:10001 /data/blobs

# /data/blobs is where stored PDFs live when BLOB_ROOT points at it (P9d). It is the ONE path the
# app user may write. Compose mounts a named volume here, and a new named volume takes the owner of
# the folder it is mounted over, which is why the folder is created and handed over in the image.

WORKDIR /app

# Everything below stays owned by root and is not writable by the user the app runs as, so a
# compromised process cannot change the code it is running.
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

USER 10001

EXPOSE 8000

# Liveness only: "is this process serving HTTP?". It deliberately does not touch the database, so a
# database blip does not make the container look dead. Readiness is /api/readyz. The slim image has
# no curl, so this uses Python's standard library.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/healthz', timeout=2)"]

# Exec form, so uvicorn is PID 1 and receives SIGTERM directly (a graceful stop). No --reload, and no
# --proxy-headers: the backend deliberately never trusts X-Forwarded-* (ADR 012). There is no
# ENTRYPOINT, so `command:` can run a different program from this same image.
CMD ["uvicorn", "app.main:create_app", "--factory", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
