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

# Stage forked PHP trees over the upstream openemr image's copies.
#
# Why mirror whole trees and not specific files: the local repo forks
# upstream openemr at every layer (~766 files diverge in src/ alone,
# many with caller/callee signature changes). Hand-listing files turned
# every deploy into a whack-a-mole on signature mismatches — e.g.
# DocumentRestController calling a 4-arg DocumentService::insertAtPath
# while upstream defines it as 3-arg, or local Document.class.php having
# an $eid parameter the upstream version lacks. Shipping the whole
# forked trees guarantees the deployed code matches what the local repo
# tests against.
#
# Trees shipped:
#   src/      — modern PSR-4 (OpenEMR\ namespace), heaviest divergence
#   library/  — legacy procedural PHP (Document.class.php and friends)
#   apis/     — Standard REST + FHIR route definitions
#
# Tree intentionally NOT shipped:
#   interface/ — UI layer (~69 MB). Works under upstream code today; if
#                a UI-level fork divergence surfaces, add a cp -a line
#                for it here. Adding ~2x to the build context is the
#                tradeoff to avoid.
#
# Tradeoff: this masks any upstream security patches in src/library/apis
# under the local versions. Rebase against upstream periodically.
#
# See also: agentforge-docs/DEPLOYMENT.md "Shipping the forked tree".

PATCHES_DEST="${CTX}/patches"
rm -rf "${PATCHES_DEST}"
mkdir -p "${PATCHES_DEST}"
cp -a "${REPO_ROOT}/src"     "${PATCHES_DEST}/src"
cp -a "${REPO_ROOT}/library" "${PATCHES_DEST}/library"
cp -a "${REPO_ROOT}/apis"    "${PATCHES_DEST}/apis"
echo "==> Staged forked trees: $(find "${PATCHES_DEST}" -type f | wc -l | tr -d ' ') file(s) ($(du -sh "${PATCHES_DEST}" | cut -f1))"

echo "==> Deploying openemr from ${CTX}"
railway up \
    --service openemr \
    --environment production \
    --detach \
    --path-as-root \
    "${CTX}"

echo "==> Deploy triggered. Watch logs with: railway logs --service openemr"
