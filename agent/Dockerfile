# Multi-stage build for the Co-Pilot Python service.
# Pattern from https://docs.astral.sh/uv/guides/integration/docker — pinned uv
# tag for reproducibility, separated dependency vs project install layers, and
# a slim runtime stage that carries only the .venv (no source-bind, no uv).

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.5.18

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

# Pull uv from the official image so the version is pinned to UV_VERSION.
COPY --from=ghcr.io/astral-sh/uv:0.5.18 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first (separate layer, cached at the Docker-layer
# level by lockfile contents). We deliberately don't use BuildKit cache
# mounts here — Railway requires a service-scoped id prefix
# (s/<service-id>-<target>) that would bake a Railway-specific value into
# this Dockerfile and break portability to other platforms. Docker layer
# caching is sufficient for our needs.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Copy the project source and install the project itself.
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy only the virtualenv from the builder. Source isn't shipped — the
# package is installed into .venv as a wheel via `uv sync --no-editable`.
COPY --from=builder /app/.venv /app/.venv

# Railway injects $PORT at runtime. Default to 8000 for local docker runs.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT is interpolated at start. uvicorn picks the production
# loop automatically (uvloop) since we install the [standard] extra.
CMD uvicorn copilot.server:app --host 0.0.0.0 --port ${PORT}
