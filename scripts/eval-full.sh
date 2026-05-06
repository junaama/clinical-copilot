#!/usr/bin/env bash
# Live-VLM eval target — separate from the push gate.
#
# Runs the W2 fixture eval suite AND the existing live-VLM extraction
# tests under ``agent/tests/test_vlm_extraction.py`` (which round-trips
# the lipid-panel fixture PDF through the real Sonnet vision model).
# This catches model drift without blocking every push.
#
# Cost: a single Sonnet vision call per fixture document. Run when
# upgrading the model pin or after bumping ``ANTHROPIC_API_KEY``.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT/agent"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ANTHROPIC_API_KEY not set — live-VLM tests will skip."
fi

echo "==> fixture-based gate (fast)"
uv run python -m copilot.eval.w2_baseline_cli check

echo
echo "==> live VLM extraction tests (slow, costs API spend)"
COPILOT_TEST_LIVE_VLM=1 uv run pytest tests/test_vlm_extraction.py -v
