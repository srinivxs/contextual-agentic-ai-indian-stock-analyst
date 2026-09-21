# The web image: the Next.js static export (ADR 006) served by nginx.
#
# Two stages. The BUILDER runs the real production build and produces plain files in out/. The
# RUNTIME stage contains only those files, the nginx configuration and nginx itself: no Node, no
# npm, no node_modules, no sources. Nothing configurable or secret is baked in (the export has no
# runtime configuration; the browser talks to same-origin /api/v1).

# ---- builder ----------------------------------------------------------------------------------
FROM node:22.23.2-alpine3.24 AS builder

ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

# Only the manifests first: the install layer is rebuilt when dependencies change, not on every
# edit to a page. `npm ci` installs exactly what package-lock.json says (and fails if they differ).
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Then everything else. `next build` type-checks tests/ and the config files as well as src/, so the
# whole project is copied; .dockerignore keeps out secrets, dependencies and build output.
COPY . .
RUN npm run build

# ---- runtime ----------------------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.31.6-alpine3.24 AS runtime

# This base image runs as the unprivileged user nginx (uid 101) on port 8080, and keeps its pid and
# temporary files in /tmp, so the container also works with a read-only root filesystem.
# The files stay owned by root and are not writable by the user that serves them.
COPY --from=builder /app/out /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

# "Is nginx serving?" The base image has BusyBox wget and no curl.
HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
