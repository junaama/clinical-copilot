#!/usr/bin/env bash
# Deploy OpenEMR (with copilot-launcher module) to Railway.
# Stages the PHP module into the build context, then deploys.
# Run from anywhere: bash scripts/deploy-openemr.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTX="${REPO_ROOT}/docker/openemr-railway"
SRC="${REPO_ROOT}/interface/modules/custom_modules/oe-module-copilot-launcher"
DEST="${CTX}/oe-module-copilot-launcher"

# Stage the copilot-launcher module into the build context.
if [[ -d "${SRC}" ]]; then
    rm -rf "${DEST}"
    cp -R "${SRC}" "${DEST}"
    echo "==> Staged copilot-launcher module"
else
    echo "WARN: copilot-launcher source not found at ${SRC}" >&2
    echo "      Deploying without module update." >&2
fi

# Stage local patches to upstream OpenEMR PHP. Each patch file mirrors
# its upstream path under ${CTX}/patches/ so the Dockerfile can COPY
# the tree onto the image at /var/www/localhost/htdocs/openemr/.
PATCHES_DEST="${CTX}/patches"
rm -rf "${PATCHES_DEST}"
mkdir -p "${PATCHES_DEST}/src/Common/Session"
cp "${REPO_ROOT}/src/Common/Session/SessionConfigurationBuilder.php" \
   "${PATCHES_DEST}/src/Common/Session/SessionConfigurationBuilder.php"
mkdir -p "${PATCHES_DEST}/src/Services"
cp "${REPO_ROOT}/src/Services/DocumentService.php" \
   "${PATCHES_DEST}/src/Services/DocumentService.php"
mkdir -p "${PATCHES_DEST}/src/RestControllers"
cp "${REPO_ROOT}/src/RestControllers/DocumentRestController.php" \
   "${PATCHES_DEST}/src/RestControllers/DocumentRestController.php"
echo "==> Staged PHP patches: $(find "${PATCHES_DEST}" -type f | wc -l | tr -d ' ') file(s)"

echo "==> Deploying openemr from ${CTX}"
railway up \
    --service openemr \
    --environment production \
    --detach \
    --path-as-root \
    "${CTX}"

echo "==> Deploy triggered. Watch logs with: railway logs --service openemr"
