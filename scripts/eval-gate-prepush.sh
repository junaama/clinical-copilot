#!/usr/bin/env bash
# Pre-push gate that runs the W2 fixture-based eval suite when the push
# touches files that could affect agent behavior.
#
# Skip rules: if every changed file matches one of the always-skip path
# patterns (docs, UI, scripts, agentforge-docs, README, *.md), the gate
# is bypassed. Otherwise the gate runs and blocks the push on regression.
#
# This script is idempotent and safe to invoke from anywhere — it
# resolves the repo root via ``git rev-parse``.
#
# Install it via the committed ``hooks/pre-push`` wrapper; that wrapper
# delegates here so this script remains the single source of gate logic.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Determine the range of commits being pushed. Git pipes the
# (local_ref local_sha remote_ref remote_sha) tuples to the hook on
# stdin. We collect every local_sha in the push and diff against the
# corresponding remote_sha (or the merge-base when remote_sha is the
# zero hash).
ZERO="0000000000000000000000000000000000000000"
CHANGED_FILES=""
while read -r local_ref local_sha remote_ref remote_sha; do
    if [[ -z "${local_sha:-}" || "$local_sha" == "$ZERO" ]]; then
        continue
    fi
    if [[ "${remote_sha:-$ZERO}" == "$ZERO" ]]; then
        # New branch — diff against origin/HEAD (best-effort).
        base="$(git rev-parse origin/HEAD 2>/dev/null || echo "")"
        if [[ -z "$base" ]]; then
            base="$(git rev-list --max-parents=0 "$local_sha" | head -1)"
        fi
        diff_files="$(git diff --name-only "$base...$local_sha" || true)"
    else
        diff_files="$(git diff --name-only "$remote_sha...$local_sha" || true)"
    fi
    CHANGED_FILES="${CHANGED_FILES}${diff_files}
"
done

# Deduplicate.
CHANGED_FILES="$(printf "%s" "$CHANGED_FILES" | awk 'NF' | sort -u)"

if [[ -z "$CHANGED_FILES" ]]; then
    echo "[w2-eval-gate] no changed files in this push — skipping."
    exit 0
fi

# Files that should trigger the gate. Anything under these paths means
# agent behavior or eval data changed.
#
# ``^agent/tests/`` is included to fire the graph-integration suite
# (issue 021) on test-only edits — the unit-level w2_runner cannot catch
# graph-wiring regressions and the integration tests are the canonical
# layer for them.
TRIGGER_PATTERNS=(
    "^agent/src/"
    "^agent/evals/"
    "^agent/tests/"
    "^data/guidelines/"
    "^\.eval_baseline\.json$"
)

needs_gate=0
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    for p in "${TRIGGER_PATTERNS[@]}"; do
        if [[ "$f" =~ $p ]]; then
            needs_gate=1
            break
        fi
    done
    [[ "$needs_gate" -eq 1 ]] && break
done <<< "$CHANGED_FILES"

if [[ "$needs_gate" -eq 0 ]]; then
    echo "[w2-eval-gate] only docs/UI/config files changed — skipping."
    exit 0
fi

echo "[w2-eval-gate] running fixture eval gate..."
cd agent
if ! uv run --quiet python -m copilot.eval.w2_baseline_cli check; then
    echo "[w2-eval-gate] FAILED — push blocked. Re-run locally to debug:"
    echo "    cd agent && uv run python -m copilot.eval.w2_baseline_cli check"
    echo
    echo "If the regression is intentional and the baseline should move,"
    echo "rerun with --write to refresh .eval_baseline.json."
    exit 1
fi

# Issue 021 — graph integration tests. Sub-second; catch graph-wiring
# regressions (worker contextvars, supervisor re-dispatch, classifier
# upload sentinel, worker-on-ToolMessage) that the node-isolated
# w2_runner cannot see.
echo "[w2-eval-gate] running graph integration tests..."
if ! uv run --quiet pytest -q tests/test_graph_integration.py; then
    echo "[w2-eval-gate] FAILED — graph integration regression. Re-run locally:"
    echo "    cd agent && uv run pytest -q tests/test_graph_integration.py"
    exit 1
fi

echo "[w2-eval-gate] PASSED"
exit 0
