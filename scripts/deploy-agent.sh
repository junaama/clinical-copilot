#!/usr/bin/env bash
# Deploy the Co-Pilot agent backend to Railway.
# Run from anywhere: bash scripts/deploy-agent.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Deploying copilot-agent from ${REPO_ROOT}/agent"
railway up \
    --service copilot-agent \
    --environment production \
    --detach \
    --path-as-root \
    "${REPO_ROOT}/agent"

echo "==> Deploy triggered. Watch logs with: railway logs --service copilot-agent"
