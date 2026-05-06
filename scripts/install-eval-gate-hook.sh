#!/usr/bin/env bash
# Install the W2 eval-gate as the repo's pre-push hook.
#
# The hook itself lives in ``scripts/eval-gate-prepush.sh`` (committed
# so the team shares one source of truth). This installer creates a
# symlink at ``.git/hooks/pre-push`` pointing at it.
#
# Re-run this script after ``git clone`` to opt in.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

src="scripts/eval-gate-prepush.sh"
dst=".git/hooks/pre-push"

if [[ ! -f "$src" ]]; then
    echo "missing $src" >&2
    exit 1
fi

chmod +x "$src"

# If something is already at $dst, back it up so we don't clobber a
# personal hook silently.
if [[ -e "$dst" && ! -L "$dst" ]]; then
    backup="$dst.backup.$(date +%Y%m%d%H%M%S)"
    echo "[install] backing up existing $dst to $backup"
    mv "$dst" "$backup"
fi

# Replace any existing symlink unconditionally — re-running the
# installer should always end with the symlink pointing at the
# committed script.
ln -sf "../../$src" "$dst"
echo "[install] linked $dst -> $src"
echo "[install] gate will run on every \`git push\`."
