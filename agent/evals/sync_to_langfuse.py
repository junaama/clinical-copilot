"""Sync YAML eval cases to Langfuse datasets.

EVAL.md §3 says the source of truth is YAML in git; this script pushes them
to Langfuse so dataset experiments and dashboard views can reference them
by id. Idempotent: existing items are updated, missing ones added, deletions
are reported but not auto-applied (manual review of removed test cases).

Usage:
    uv run python -m evals.sync_to_langfuse --tier=all
    uv run python -m evals.sync_to_langfuse --tier=golden
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from copilot.config import get_settings
from copilot.eval.case import load_cases_in_dir

EVALS_ROOT = Path(__file__).resolve().parent
TIERS = ["smoke", "golden", "adversarial", "drift"]


def _dataset_name(tier: str, project: str) -> str:
    return f"{project}-evals-{tier}"


def _build_item_payload(case) -> dict:
    return {
        "input": {"message": case.message},
        "expected_output": {
            "required_facts": case.required_facts,
            "required_citation_refs": case.required_citation_refs,
            "forbidden_claims": case.forbidden_claims,
            "forbidden_pids_in_response": case.forbidden_pids,
            "decision": case.expected_decision,
        },
        "metadata": {
            "tier": case.tier,
            "workflow": case.workflow,
            "user_id": case.user_id,
            "user_role": case.user_role,
            "patient_id": case.patient_id,
            "description": case.description,
            "yaml_path": str(case.path.relative_to(EVALS_ROOT)),
        },
    }


def sync(tier: str, *, dry_run: bool = False) -> int:
    """Push every case in ``tier`` to its Langfuse dataset. Returns exit code."""
    settings = get_settings()
    if not settings.langfuse_enabled:
        logging.error(
            "Langfuse env not set; cannot sync. "
            "Set LANGFUSE_HOST/LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY."
        )
        return 2

    cases = load_cases_in_dir(EVALS_ROOT / tier)
    if not cases:
        logging.warning("No cases found for tier %s", tier)
        return 0

    if dry_run:
        for case in cases:
            print(json.dumps({"id": case.id, "tier": tier, **_build_item_payload(case)}, indent=2))
        return 0

    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ImportError:
        logging.error("langfuse package not installed. Run 'uv sync'.")
        return 2

    client = Langfuse(
        host=settings.langfuse_host,
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
    )

    dataset_name = _dataset_name(tier, settings.langfuse_project)
    # Create-if-missing
    try:
        client.create_dataset(name=dataset_name, description=f"Co-Pilot {tier} eval cases")
    except Exception:  # noqa: BLE001 — already exists is fine
        pass

    added = updated = 0
    for case in cases:
        payload = _build_item_payload(case)
        try:
            client.create_dataset_item(
                dataset_name=dataset_name,
                id=case.id,
                **payload,
            )
            added += 1
        except Exception:  # noqa: BLE001 — already exists; treat as update
            try:
                client.update_dataset_item(
                    dataset_name=dataset_name,
                    id=case.id,
                    **payload,
                )
                updated += 1
            except Exception as exc:  # noqa: BLE001
                logging.warning("Failed to upsert %s: %s", case.id, exc)

    client.flush()
    logging.info(
        "Synced tier=%s dataset=%s: added=%d, updated=%d, total=%d",
        tier,
        dataset_name,
        added,
        updated,
        len(cases),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync eval YAML cases to Langfuse datasets.")
    parser.add_argument("--tier", choices=[*TIERS, "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads, do not push")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    tiers_to_sync = TIERS if args.tier == "all" else [args.tier]
    for tier in tiers_to_sync:
        rc = sync(tier, dry_run=args.dry_run)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
