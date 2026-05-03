# Multi-stage build for the Co-Pilot service.
#
# Build context is the repo root (set by ``scripts/deploy-agent.sh`` and
# ``agent/railway.toml``) so the image can bundle both the Python agent and
# the static copilot-ui build into a single origin. Same-origin avoids
# third-party-cookie / CORS-credentials issues that broke the cross-domain
# Railway split (UI on copilot-ui-production, agent on copilot-agent-...).
#
# Stages:
#   1. ui-builder      — Node, ``npm run build`` of copilot-ui → /ui/dist
#   2. python-builder  — uv sync of the agent package → /app/.venv
#   3. runtime         — slim Python carrying .venv + /app/static (the UI)

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=20
ARG UV_VERSION=0.5.18

# ---------- ui-builder ----------
FROM node:${NODE_VERSION}-alpine AS ui-builder

WORKDIR /ui

# Dependency layer first so source edits don't bust the npm cache.
COPY copilot-ui/package.json copilot-ui/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Same-origin deployment ⇒ blank ``VITE_AGENT_URL`` so the API client uses
# relative paths (``/chat``, ``/me``, ``/auth/login``). FHIR base is the
# OpenEMR FHIR endpoint; pass through if Railway has it set.
ARG VITE_AGENT_URL=""
ARG VITE_FHIR_BASE=""
ENV VITE_AGENT_URL=${VITE_AGENT_URL} \
    VITE_FHIR_BASE=${VITE_FHIR_BASE}

COPY copilot-ui/tsconfig.json copilot-ui/vite.config.ts copilot-ui/index.html ./
COPY copilot-ui/src ./src
RUN npm run build

# ---------- python-builder ----------
FROM python:${PYTHON_VERSION}-slim AS python-builder

COPY --from=ghcr.io/astral-sh/uv:0.5.18 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY agent/pyproject.toml agent/uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY agent/src ./src
RUN uv sync --locked --no-dev --no-editable

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=python-builder /app/.venv /app/.venv
# UI bundle. server.py mounts /app/static at "/" via StaticFiles.
COPY --from=ui-builder /ui/dist /app/static

ENV PORT=8000 \
    COPILOT_STATIC_DIR=/app/static
EXPOSE 8000

CMD uvicorn copilot.server:app --host 0.0.0.0 --port ${PORT}
