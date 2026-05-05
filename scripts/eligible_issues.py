#!/usr/bin/env python3
"""Compute which top-level issues/*.md tasks are eligible to be claimed
by an AFK worker, based on `depends-on` frontmatter.

A task is eligible iff every basename listed in its `depends-on` frontmatter
exists in issues/done/. A task with no frontmatter or no depends-on is
eligible by default.

Frontmatter format (YAML-ish, parsed leniently):

    ---
    depends-on:
      - 001-some-task
      - 002-other-task
    ---

Or inline list:

    ---
    depends-on: [001-some-task, 002-other-task]
    ---

Or single value:

    ---
    depends-on: 001-some-task
    ---

Dependency tokens are basenames WITHOUT the .md extension. A dep is
"satisfied" iff issues/done/<dep>.md exists.

Usage:
    eligible_issues.py            # human-readable summary
    eligible_issues.py --json     # machine-readable JSON for shell scripts
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def parse_deps(text: str) -> list[str]:
    """Extract depends-on entries from frontmatter. Returns [] if none."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return []

    in_block = False
    deps: list[str] = []
    for raw in m.group(1).splitlines():
        stripped = raw.strip()
        if not in_block:
            if not stripped.startswith("depends-on"):
                continue
            # Match `depends-on:` exactly (allow whitespace before colon).
            colon_idx = stripped.find(":")
            if colon_idx < 0 or stripped[:colon_idx].strip() != "depends-on":
                continue
            rest = stripped[colon_idx + 1 :].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                return [_clean(x) for x in inner.split(",") if x.strip()]
            if rest:
                return [_clean(rest)]
            in_block = True
            continue
        # Inside block list.
        if stripped == "":
            continue
        if stripped.startswith("-"):
            deps.append(_clean(stripped[1:]))
            continue
        # Hit another key → block ended.
        break
    return deps


def _clean(token: str) -> str:
    return token.strip().strip('"').strip("'")


def main(argv: list[str]) -> int:
    repo = Path(__file__).resolve().parent.parent
    issues_dir = repo / "issues"
    done_dir = issues_dir / "done"
    in_progress_dir = issues_dir / "in-progress"

    done_basenames = {p.stem for p in done_dir.glob("*.md")} if done_dir.is_dir() else set()
    in_progress = (
        sorted(p.name for p in in_progress_dir.glob("*.md"))
        if in_progress_dir.is_dir()
        else []
    )

    eligible: list[dict[str, str]] = []
    blocked: list[dict[str, object]] = []

    for path in sorted(issues_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        deps = parse_deps(text)
        missing = [d for d in deps if d not in done_basenames]
        if missing:
            blocked.append({"basename": path.name, "missing_deps": missing})
        else:
            eligible.append({"basename": path.name, "content": text})

    state = {"eligible": eligible, "blocked": blocked, "in_progress": in_progress}

    if "--json" in argv:
        json.dump(state, sys.stdout)
        sys.stdout.write("\n")
        return 0

    print("ELIGIBLE:")
    if not eligible:
        print("  (none)")
    for item in eligible:
        print(f"  {item['basename']}")
    print()
    print("BLOCKED (dependency not yet in issues/done/):")
    if not blocked:
        print("  (none)")
    for item in blocked:
        missing = ", ".join(item["missing_deps"])  # type: ignore[arg-type]
        print(f"  {item['basename']} -> waiting on: {missing}")
    print()
    print("IN PROGRESS:")
    if not in_progress:
        print("  (none)")
    for n in in_progress:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
