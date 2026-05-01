# Multi-stage build for the Co-Pilot React UI.
# Stage 1 builds the static bundle with Vite. Stage 2 serves dist/ with `serve`
# in SPA mode (-s flag) so client-side routes (/, /launch) all resolve to
# index.html. Vite inlines `import.meta.env.VITE_*` at BUILD time, so the
# VITE_* values must be passed as build args from Railway service env vars.

ARG NODE_VERSION=20

# ---------- builder ----------
FROM node:${NODE_VERSION}-alpine AS builder

WORKDIR /app

# Install dependencies first for better Docker layer caching.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Build args → env vars → Vite inlines them into dist/.
ARG VITE_AGENT_URL=""
ARG VITE_FHIR_BASE=""
ENV VITE_AGENT_URL=${VITE_AGENT_URL} \
    VITE_FHIR_BASE=${VITE_FHIR_BASE}

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---------- runtime ----------
FROM node:${NODE_VERSION}-alpine AS runtime

WORKDIR /app

# `serve` is a tiny static file server with first-class SPA support.
RUN npm install --global serve@14.2.4

COPY --from=builder /app/dist ./dist

# Railway injects $PORT; bind to 0.0.0.0 so the container is reachable.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "serve -s dist -l tcp://0.0.0.0:${PORT}"]
