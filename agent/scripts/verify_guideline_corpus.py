"""Verify local and indexed guideline-corpus coverage.

This is a final-QA helper for the Week 2 RAG corpus. It checks that the
expected guideline PDFs exist locally, reports local chunk counts, and,
when ``CHECKPOINTER_DSN`` is set, compares those counts with the
``guideline_chunks`` table.
"""

from __future__ import annotations

import os
from pathlib import Path

from copilot.retrieval.corpus import chunk_guideline

EXPECTED_GUIDELINES: tuple[str, ...] = (
    "ada-diabetes-glycemic-2024",
    "Heidenreich-et-al_2022_AHA-ACC-HFSA-Guideline-for-the-Management-of-Heart-Failure-A-Report-of-the-American-College-of-Cardiology-American-Heart-Association-Joint-Committee-on-Clinical-Practice-Guidelines",
    "implementing-an-antibiotic-stewardship-program-guidelines-by-the-infectious-diseases-society-of-america-and-the-society-for-healthcare-epidemiology-of-america",
    "jnc8-hypertension-2014",
    "kdigo-ckd-2024",
)


def _corpus_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "guidelines"


def _indexed_counts() -> dict[str, int] | None:
    dsn = os.environ.get("CHECKPOINTER_DSN", "").strip()
    if not dsn:
        return None

    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT guideline, count(*)
            FROM guideline_chunks
            GROUP BY guideline
            ORDER BY guideline
            """
        )
        return {str(name): int(count) for name, count in cur.fetchall()}


def main() -> int:
    root = _corpus_dir()
    local_counts: dict[str, int] = {}
    missing: list[str] = []

    for guideline in EXPECTED_GUIDELINES:
        path = root / f"{guideline}.pdf"
        if not path.exists():
            missing.append(guideline)
            continue
        local_counts[guideline] = len(chunk_guideline(path, guideline))

    print("local guideline PDFs/chunks:")
    for guideline in EXPECTED_GUIDELINES:
        count = local_counts.get(guideline)
        marker = "MISSING" if count is None else str(count)
        print(f"  {guideline}: {marker}")

    if missing:
        print("\nmissing local PDFs:")
        for guideline in missing:
            print(f"  - {guideline}.pdf")
        return 1

    indexed = _indexed_counts()
    if indexed is None:
        print("\nCHECKPOINTER_DSN not set; skipped DB index verification.")
        return 0

    print("\nindexed guideline chunks:")
    failed = False
    for guideline in EXPECTED_GUIDELINES:
        local = local_counts[guideline]
        indexed_count = indexed.get(guideline, 0)
        status = "OK" if indexed_count >= local else "MISSING"
        print(f"  {guideline}: {indexed_count}/{local} {status}")
        failed = failed or indexed_count < local

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
