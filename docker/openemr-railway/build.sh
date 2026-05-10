#!/usr/bin/env bash
# Stage the Co-Pilot modules into the Railway build context, then deploy.
# Run from the repo root, e.g.: `bash docker/openemr-railway/build.sh`.
#
# Exits non-zero on the first error; safe to re-run (idempotent).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CTX="${REPO_ROOT}/docker/openemr-railway"
MODULE_SRC_ROOT="${REPO_ROOT}/interface/modules/custom_modules"
MODULE_DEST_ROOT="${CTX}/custom_modules"
DASHBOARD_FRONTEND="${REPO_ROOT}/frontend/patient-dashboard"
DASHBOARD_ASSETS="${REPO_ROOT}/public/assets/patient-dashboard"

if ! compgen -G "${MODULE_SRC_ROOT}/oe-module-copilot-*" > /dev/null; then
    echo "module source missing: ${MODULE_SRC_ROOT}/oe-module-copilot-*" >&2
    exit 1
fi

# Refresh the staged copies. Removing first avoids stale files from a
# previous build (e.g. a file deleted in the source tree).
rm -rf "${MODULE_DEST_ROOT}"
mkdir -p "${MODULE_DEST_ROOT}"
for module in "${MODULE_SRC_ROOT}"/oe-module-copilot-*; do
    cp -R "${module}" "${MODULE_DEST_ROOT}/$(basename "${module}")"
done

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

echo "staged Co-Pilot modules → ${MODULE_DEST_ROOT}"
echo "staged ${DASHBOARD_ASSETS} → ${CTX}/patches/public/assets/patient-dashboard"
echo "deploy with:"
echo "    railway up --service openemr --ci --path-as-root docker/openemr-railway"
