"""Pre-push hook wrapper tests.

These tests exercise the documented hook install path rather than the
underlying eval modules directly. The heavy eval command is represented by a
fake ``uv`` executable so the hook behavior stays deterministic and fast while
still flowing through ``hooks/pre-push`` and ``scripts/eval-gate-prepush.sh``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(
    args: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_fake_uv(bin_dir: Path) -> None:
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "run" ]]; then
    shift
fi
if [[ "${1:-}" == "--quiet" ]]; then
    shift
fi

case "${1:-}" in
    python)
        python - <<'PY'
import json
import pathlib
import sys

fixture = pathlib.Path.cwd() / "evals" / "w2" / "fixtures" / "lab_chen_lipid.json"
data = json.loads(fixture.read_text())
if not data.get("results"):
    print("known-bad extraction fixture: results must not be empty", file=sys.stderr)
    sys.exit(42)
PY
        ;;
    pytest)
        exit 0
        ;;
    *)
        echo "unexpected fake uv command: $*" >&2
        exit 64
        ;;
esac
""",
    )
    uv.chmod(0o755)


def _init_hook_repo(tmp_path: Path, *, broken_fixture: bool) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "test@example.test"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)

    (repo / "hooks").mkdir()
    (repo / "scripts").mkdir()
    (repo / "agent" / "evals" / "w2" / "fixtures").mkdir(parents=True)
    (repo / "agent" / "tests").mkdir()

    shutil.copy2(REPO_ROOT / "hooks" / "pre-push", repo / "hooks" / "pre-push")
    shutil.copy2(
        REPO_ROOT / "scripts" / "eval-gate-prepush.sh",
        repo / "scripts" / "eval-gate-prepush.sh",
    )
    (repo / "scripts" / "eval-gate-prepush.sh").chmod(0o755)

    fixture = repo / "agent" / "evals" / "w2" / "fixtures" / "lab_chen_lipid.json"
    fixture.write_text(json.dumps({"results": [{"test_name": "Total Cholesterol"}]}))
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "test: initial clean fixture"], repo)
    base = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    payload = {"results": []} if broken_fixture else {"results": [{"test_name": "LDL"}]}
    fixture.write_text(json.dumps(payload))
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "test: update eval fixture"], repo)
    head = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    return repo, base, head


def _invoke_installed_hook(
    repo: Path,
    base: str,
    head: str,
    fake_bin: Path,
) -> subprocess.CompletedProcess[str]:
    install = _run(
        ["sh", "-c", "cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push"],
        repo,
    )
    assert install.returncode == 0, install.stderr

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    stdin = f"refs/heads/main {head} refs/heads/main {base}\n"
    return _run([".git/hooks/pre-push"], repo, env=env, input_text=stdin)


def test_installed_pre_push_hook_passes_on_clean_extraction_fixture(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_uv(fake_bin)
    repo, base, head = _init_hook_repo(tmp_path, broken_fixture=False)

    result = _invoke_installed_hook(repo, base, head, fake_bin)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[w2-eval-gate] PASSED" in result.stdout


def test_installed_pre_push_hook_blocks_known_bad_extraction_fixture(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_uv(fake_bin)
    repo, base, head = _init_hook_repo(tmp_path, broken_fixture=True)

    result = _invoke_installed_hook(repo, base, head, fake_bin)

    assert result.returncode != 0
    assert "known-bad extraction fixture" in result.stderr
    assert "[w2-eval-gate] FAILED" in result.stdout
