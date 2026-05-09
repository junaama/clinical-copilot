#!/usr/bin/env bash
# Build the modern patient dashboard and, when the development OpenEMR
# container is running, copy the assets into its public/assets volume.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_FRONTEND="${REPO_ROOT}/frontend/patient-dashboard"
DASHBOARD_ASSETS="${REPO_ROOT}/public/assets/patient-dashboard"
DEV_COMPOSE_DIR="${REPO_ROOT}/docker/development-easy"
OPENEMR_ASSETS_DIR="/var/www/localhost/htdocs/openemr/public/assets/patient-dashboard"

cd "${DASHBOARD_FRONTEND}"
npm ci
npm run build

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; built host assets only."
    exit 0
fi

container_id="$(
    cd "${DEV_COMPOSE_DIR}" \
        && docker compose ps -q openemr 2>/dev/null || true
)"

if [[ -z "${container_id}" ]]; then
    echo "development OpenEMR container not running; built host assets only."
    exit 0
fi

docker exec "${container_id}" mkdir -p "${OPENEMR_ASSETS_DIR}"
docker cp "${DASHBOARD_ASSETS}/." "${container_id}:${OPENEMR_ASSETS_DIR}/"
echo "synced patient dashboard assets into development OpenEMR container."
