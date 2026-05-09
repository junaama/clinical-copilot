#!/usr/bin/env bash
# Install the W2 eval-gate as the repo's pre-push hook.
#
# The installable hook lives at ``hooks/pre-push``. It is a small wrapper
# that delegates to ``scripts/eval-gate-prepush.sh``, where the shared
# gate logic lives.
#
# Re-run this script after ``git clone`` to opt in.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

src="hooks/pre-push"
dst=".git/hooks/pre-push"

if [[ ! -f "$src" ]]; then
    echo "missing $src" >&2
    exit 1
fi

chmod +x "$src"
chmod +x "scripts/eval-gate-prepush.sh"

# If something is already at $dst, back it up so we don't clobber a
# personal hook silently.
if [[ -e "$dst" && ! -L "$dst" ]]; then
    backup="$dst.backup.$(date +%Y%m%d%H%M%S)"
    echo "[install] backing up existing $dst to $backup"
    mv "$dst" "$backup"
fi
if [[ -L "$dst" ]]; then
    rm "$dst"
fi

# Re-running the installer should always end with the documented wrapper
# copied into the git hook path.
cp "$src" "$dst"
chmod +x "$dst"
echo "[install] copied $src -> $dst"
echo "[install] gate will run on every \`git push\`."
