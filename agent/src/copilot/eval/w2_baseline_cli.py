"""CLI used by the W2 pre-push gate (issue 010).

Loads every YAML under ``agent/evals/w2/``, scores the five boolean
rubrics, compares the result against ``.eval_baseline.json`` at the
repo root, and exits non-zero if any rubric drops below floor or
regresses more than 5%.

Validator-unit cases score the static ``fixture_response`` against
the rubrics directly. Live cases (``mode: live``) invoke the real
agent and score its actual response. The CLI dispatches both kinds
through ``score_w2_cases_async`` when at least one live case is
present.

Two operating modes:

* ``python -m copilot.eval.w2_baseline_cli check``  — gate mode, used by
  the pre-push hook. Prints a one-shot report and exits 0 / 1.
* ``python -m copilot.eval.w2_baseline_cli --write`` — regenerates
  ``.eval_baseline.json`` from the current pass rates. Use this when
  the case set legitimately changed (added cases, flipped a case to
  live mode, deliberate baseline raise) and the new rates should become
  the bar.

The CLI is deliberately thin — all rubric / runner / baseline logic
lives in the existing modules so the hook stays trivial.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .baseline import detect_regression, load_baseline, render_report, write_baseline
from .llm_judge import LLMJudgeConfigurationError, ensure_llm_judge_ready
from .w2_runner import (
    MODE_LIVE,
    compute_pass_rates,
    llm_judge_enabled,
    load_w2_cases_in_dir,
    register_schema,
    score_w2_cases,
    score_w2_cases_async,
)
from .w2_schemas import register_w2_eval_schemas


def _eval_dir(repo_root: Path) -> Path:
    return repo_root / "agent" / "evals" / "w2"


def _baseline_path(repo_root: Path) -> Path:
    return repo_root / ".eval_baseline.json"


def _run(repo_root: Path) -> tuple[dict[str, float], int, int]:
    # Live cases route through the agent's fixture-FHIR path the same
    # way smoke/golden do (see ``evals/conftest.py``). Without this the
    # live W2 personas (dr_lopez etc.) hit live OpenEMR and the
    # CareTeam gate denies every call before the agent can answer.
    os.environ.setdefault("USE_FIXTURE_FHIR", "1")
    if llm_judge_enabled():
        ensure_llm_judge_ready()
    register_w2_eval_schemas(register_schema)
    cases = load_w2_cases_in_dir(_eval_dir(repo_root))
    if any(c.mode == MODE_LIVE for c in cases):
        results = asyncio.run(score_w2_cases_async(cases))
    else:
        results = score_w2_cases(cases)
    rates = compute_pass_rates(results)
    # Separate deterministic (validator_unit) failures from live failures.
    # Live cases invoke the real LLM and produce non-deterministic
    # responses — their per-case expected verdicts can't be pinned
    # reliably. The rate regression check catches systematic degradation
    # across the full suite; the case-level hard-fail is only meaningful
    # for deterministic validator_unit cases.
    fixture_failures = [
        r for r in results if not r.case_passed and r.case.mode != MODE_LIVE
    ]
    live_failures = [
        r for r in results if not r.case_passed and r.case.mode == MODE_LIVE
    ]
    return rates, len(fixture_failures), len(live_failures)


def cmd_check(repo_root: Path) -> int:
    """Run the gate. Returns ``0`` on pass, ``1`` on regression."""
    try:
        rates, fixture_failures, live_failures = _run(repo_root)
    except LLMJudgeConfigurationError as exc:
        print(f"W2 LLM judge configuration error: {exc}")
        return 1
    baseline = load_baseline(_baseline_path(repo_root))
    verdict = detect_regression(rates, baseline)
    print(render_report(verdict))
    if fixture_failures > 0:
        print(f"\n{fixture_failures} fixture case(s) failed their declared expected verdict.")
    if live_failures > 0:
        print(f"\n{live_failures} live case(s) had verdict mismatches (non-blocking; "
              "live responses are non-deterministic).")
    if not verdict.passed or fixture_failures > 0:
        return 1
    return 0


def cmd_write(repo_root: Path) -> int:
    """Persist the current per-rubric rates as the new baseline."""
    try:
        rates, fixture_failures, live_failures = _run(repo_root)
    except LLMJudgeConfigurationError as exc:
        print(f"W2 LLM judge configuration error: {exc}")
        return 1
    if fixture_failures > 0:
        print(
            f"refusing to write baseline: {fixture_failures} fixture case(s) failed"
            " their expected verdict. Fix the cases first."
        )
        return 1
    if live_failures > 0:
        print(
            f"note: {live_failures} live case(s) had verdict mismatches "
            "(non-blocking for baseline write)."
        )
    write_baseline(
        _baseline_path(repo_root),
        rates,
        notes=(
            "W2 fixture-based eval gate baseline. Regenerate with "
            "`python -m copilot.eval.w2_baseline_cli --write`."
        ),
    )
    print(f"wrote baseline to {_baseline_path(repo_root)}: {rates}")
    return 0


def _resolve_repo_root() -> Path:
    """``__file__`` is .../agent/src/copilot/eval/w2_baseline_cli.py."""
    return Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="w2-eval-gate")
    parser.add_argument(
        "command",
        choices=("check", "write"),
        nargs="?",
        default="check",
        help="check (default) runs the gate; write regenerates the baseline.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="alias for the ``write`` subcommand.",
    )
    args = parser.parse_args(argv)
    repo_root = _resolve_repo_root()
    cmd = "write" if args.write else args.command
    if cmd == "write":
        return cmd_write(repo_root)
    return cmd_check(repo_root)


if __name__ == "__main__":
    sys.exit(main())
