#!/usr/bin/env bash
# Deploy the Co-Pilot UI to Railway.
# Run from anywhere: bash scripts/deploy-ui.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Deploying copilot-ui from ${REPO_ROOT}/copilot-ui"
railway up \
    --service copilot-ui \
    --environment production \
    --detach \
    --path-as-root \
    "${REPO_ROOT}/copilot-ui"

echo "==> Deploy triggered. Watch logs with: railway logs --service copilot-ui"
