"""W2 push-gate test (issue 010).

Loads every YAML under ``agent/evals/w2/``, scores all five boolean
rubrics, asserts every case meets its declared ``expected`` verdict,
and compares the per-rubric pass rates against the committed
``.eval_baseline.json`` at the repo root via ``detect_regression``.

Validator-unit cases (the default, used for cases whose precondition
isn't reproducible against the agent's fixture data) score the static
``fixture_response`` against the rubrics. Live cases (``mode: live``)
invoke the real agent and score its actual response. Both modes share
the same rubric functions so a regression in either mode trips the
same code; the gate's pass rates aggregate over both.

The validator-unit subset is fast (<1s, no I/O). Live cases are gated
on ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` and call the LLM, so the
gate is opt-in via env when the test runner doesn't have a key. With
the keys present, full gate runs cost on the order of cents per run
(small prompts, small responses, gpt-4o-mini default).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from copilot.eval.baseline import detect_regression, load_baseline, render_report
from copilot.eval.w2_evaluators import GATE_THRESHOLDS_W2, RUBRIC_NAMES
from copilot.eval.w2_runner import (
    MODE_LIVE,
    compute_pass_rates,
    load_w2_cases_in_dir,
    register_schema,
    score_w2_cases,
    score_w2_cases_async,
)
from copilot.eval.w2_schemas import register_w2_eval_schemas

# Force fixture-FHIR mode for the gate run the same way ``evals/conftest.py``
# does for smoke/golden — without it, live W2 cases route to live OpenEMR
# and fail closed on every CareTeam check (W2 personas exist as fixture
# users only). The LLM-backed factually_consistent judge has its own targeted
# tests; this deterministic gate suite preserves the regex baseline behavior.
os.environ.setdefault("USE_FIXTURE_FHIR", "1")
os.environ["EVAL_LLM_JUDGE_ENABLED"] = "false"

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "agent" / "evals" / "w2"
BASELINE_PATH = REPO_ROOT / ".eval_baseline.json"

_LIVE_LLM_KEY_PRESENT = bool(
    os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
)


@pytest.fixture(scope="module", autouse=True)
def _register_schemas() -> None:
    """Make ``LabExtraction`` / ``IntakeExtraction`` resolvable from YAML."""
    register_w2_eval_schemas(register_schema)


def _has_live_cases(cases) -> bool:
    return any(c.mode == MODE_LIVE for c in cases)


@pytest.fixture(scope="module")
def _scored_cases():
    cases = load_w2_cases_in_dir(EVAL_DIR)
    assert len(cases) == 50, (
        f"expected 50 W2 cases, found {len(cases)} — "
        f"see PRD acceptance criteria"
    )
    if _has_live_cases(cases) and not _LIVE_LLM_KEY_PRESENT:
        pytest.skip(
            "W2 gate has live cases but no ANTHROPIC_API_KEY/OPENAI_API_KEY in env; "
            "set one to run the live tier or run validator-unit cases only"
        )
    if _has_live_cases(cases):
        return asyncio.run(score_w2_cases_async(cases))
    return score_w2_cases(cases)


def test_every_case_meets_its_expected_verdict(_scored_cases) -> None:
    """Every case_passed=True — i.e. every rubric matched its expected.

    A case with ``expected.schema_valid: false`` and a malformed fixture
    only passes when the rubric correctly flags the regression. So the
    aggregate ``case_passed`` flag is the right gate.
    """
    failures = [r for r in _scored_cases if not r.case_passed]
    if failures:
        lines = [
            f"{r.case.id}: {', '.join(r.failures)}" for r in failures
        ]
        pytest.fail("W2 cases failed:\n  " + "\n  ".join(lines))


def test_per_rubric_pass_rate_meets_floor(_scored_cases) -> None:
    """Per-rubric pass rate clears the absolute floor in
    ``GATE_THRESHOLDS_W2`` for every rubric."""
    rates = compute_pass_rates(_scored_cases)
    below = {n: r for n, r in rates.items() if r < GATE_THRESHOLDS_W2[n]}
    assert not below, (
        f"rubrics below floor: {below}; rates={rates}"
    )


def test_pass_rates_do_not_regress_against_baseline(_scored_cases) -> None:
    """Compare against the committed ``.eval_baseline.json``.

    A missing baseline file is acceptable — that's the fresh-baseline
    state the comparator handles. The check is only fail-the-gate when
    the file exists and a rubric dropped more than 5% from it.
    """
    rates = compute_pass_rates(_scored_cases)
    baseline = load_baseline(BASELINE_PATH)
    verdict = detect_regression(rates, baseline)
    if not verdict.passed:
        pytest.fail(render_report(verdict))


def test_case_count_distribution_matches_prd(_scored_cases) -> None:
    """PRD calls for the specific category mix; exposes drift."""
    by_category: dict[str, int] = {}
    for result in _scored_cases:
        by_category[result.case.category] = by_category.get(result.case.category, 0) + 1
    expected = {
        "lab_extraction": 10,
        "intake_extraction": 8,
        "evidence_retrieval": 8,
        "supervisor_routing": 6,
        "citation_contract": 6,
        "safe_refusal": 6,
        "no_phi_in_logs": 3,
        "regression_w1": 3,
    }
    assert by_category == expected, (
        f"category distribution drift: got {by_category}, "
        f"expected {expected}"
    )


def test_rubric_names_pinned() -> None:
    """The five-rubric set is the contract; pin it so a silent rename
    blowing up the scoreboard is caught here."""
    assert set(RUBRIC_NAMES) == {
        "schema_valid",
        "citation_present",
        "factually_consistent",
        "safe_refusal",
        "no_phi_in_logs",
    }
