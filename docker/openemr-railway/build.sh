#!/usr/bin/env bash
# Stage the Co-Pilot module into the Railway build context, then deploy.
# Run from the repo root, e.g.: `bash docker/openemr-railway/build.sh`.
#
# Exits non-zero on the first error; safe to re-run (idempotent).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CTX="${REPO_ROOT}/docker/openemr-railway"
SRC="${REPO_ROOT}/interface/modules/custom_modules/oe-module-copilot-launcher"
DEST="${CTX}/oe-module-copilot-launcher"
DASHBOARD_FRONTEND="${REPO_ROOT}/frontend/patient-dashboard"
DASHBOARD_ASSETS="${REPO_ROOT}/public/assets/patient-dashboard"

if [[ ! -d "${SRC}" ]]; then
    echo "module source missing: ${SRC}" >&2
    exit 1
fi

# Refresh the staged copy. Removing first avoids stale files from a
# previous build (e.g. a file deleted in the source tree).
rm -rf "${DEST}"
cp -R "${SRC}" "${DEST}"

if [[ -d "${DASHBOARD_FRONTEND}" ]]; then
    (
        cd "${DASHBOARD_FRONTEND}"
        npm ci
        npm run build
    )
else
    echo "patient-dashboard frontend source missing: ${DASHBOARD_FRONTEND}" >&2
    exit 1
fi

if [[ ! -d "${DASHBOARD_ASSETS}" ]]; then
    echo "patient-dashboard build assets missing: ${DASHBOARD_ASSETS}" >&2
    exit 1
fi

mkdir -p "${CTX}/patches/public/assets"
rm -rf "${CTX}/patches/public/assets/patient-dashboard"
cp -a "${DASHBOARD_ASSETS}" "${CTX}/patches/public/assets/patient-dashboard"

echo "staged ${SRC} → ${DEST}"
echo "staged ${DASHBOARD_ASSETS} → ${CTX}/patches/public/assets/patient-dashboard"
echo "deploy with:"
echo "    railway up --service openemr --ci --path-as-root docker/openemr-railway"
