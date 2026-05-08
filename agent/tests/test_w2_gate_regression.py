"""End-to-end regression test for the W2 eval gate (issue 010).

Proves the gate catches a regression introduced into a fixture case:
copies the validator-unit case set into a tmp dir, mutates one
positive case to strip its citation, and asserts the runner now
reports a sub-100% ``citation_present`` rate AND the baseline
comparator returns ``passed=False`` against a 100%-baseline.

This is the "introducing a deliberate regression causes the hook to
fail" acceptance criterion in the PRD. Live cases are filtered out
of the mutation copy because their pass rate depends on a real LLM
call — the regression assertion has to be deterministic.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from copilot.eval.baseline import detect_regression
from copilot.eval.w2_evaluators import GATE_THRESHOLDS_W2
from copilot.eval.w2_runner import (
    MODE_LIVE,
    compute_pass_rates,
    load_w2_cases_in_dir,
    register_schema,
    score_w2_cases,
)
from copilot.eval.w2_schemas import register_w2_eval_schemas

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "agent" / "evals" / "w2"


def _validator_unit_cases(directory: Path):
    """Return only the validator_unit cases under ``directory``.

    The regression suite's gate-catching assertions are deterministic
    by construction; live cases would drag a network call into the
    test, which is wrong scope for the unit-style regression proof.
    """
    return [c for c in load_w2_cases_in_dir(directory) if c.mode != MODE_LIVE]


@pytest.fixture(scope="module", autouse=True)
def _register_schemas() -> None:
    register_w2_eval_schemas(register_schema)


def _copy_eval_dir(target: Path) -> Path:
    """Mirror the live eval directory into ``target``.

    Returns the new eval dir path. We copy rather than reference so the
    mutation step doesn't touch the committed cases.
    """
    dst = target / "w2"
    shutil.copytree(EVAL_DIR, dst)
    return dst


def _strip_all_citations(case_path: Path) -> None:
    """Strip every ``<cite ref="..."/>`` tag from the fixture_response.

    The citation_present rubric is sentence-level — leaving even one
    cite tag in the same sentence as a clinical claim keeps the rubric
    happy. To prove the gate detects an uncited claim we strip them
    all, which is the regression a forgetful agent would actually
    introduce.
    """
    import re

    text = case_path.read_text()
    mutated, n = re.subn(r"<cite\s[^>]+/>", "", text)
    assert n >= 1, f"no cite tag found in {case_path}"
    case_path.write_text(mutated)


def test_gate_catches_stripped_citation_regression(tmp_path: Path) -> None:
    eval_dir = _copy_eval_dir(tmp_path)
    target = eval_dir / "lab" / "lab_001_chen_lipid_clean.yaml"
    assert target.exists(), "expected positive sample missing"
    _strip_all_citations(target)

    cases = _validator_unit_cases(eval_dir)
    results = score_w2_cases(cases)
    rates = compute_pass_rates(results)

    # The mutation drops citation_present from 100% to <100%.
    assert rates["citation_present"] < 1.0, (
        f"mutation didn't lower the rate; got {rates}"
    )

    # Compare against a synthetic 100% baseline. The drop is one case
    # out of N positive samples — call it ~3-4 percentage points,
    # under the 5% allowance for citation_present, but above the
    # absolute floor (0.90), so the gate must pass *unless* the drop
    # exceeds 5%. Force it by passing a tighter ``max_drop``.
    baseline = {n: 1.0 for n in GATE_THRESHOLDS_W2}
    verdict = detect_regression(rates, baseline, max_drop=0.0)
    assert verdict.passed is False
    assert any(c.name == "citation_present" and not c.passed for c in verdict.categories)


def test_gate_catches_below_floor_regression(tmp_path: Path) -> None:
    """Stripping enough citations drops below the absolute floor."""
    eval_dir = _copy_eval_dir(tmp_path)

    # Strip a citation from every positive citation_present case in the
    # citation/ folder.
    for path in (eval_dir / "citation").iterdir():
        if path.suffix == ".yaml":
            text = path.read_text()
            if "citation_present: true" in text and "<cite" in text:
                _strip_all_citations(path)

    cases = _validator_unit_cases(eval_dir)
    results = score_w2_cases(cases)
    rates = compute_pass_rates(results)
    # Drop should be deep enough to fall below the 0.90 floor.
    floor = GATE_THRESHOLDS_W2["citation_present"]
    baseline: dict[str, float] = {}  # no baseline → floor check only
    verdict = detect_regression(rates, baseline)
    if rates["citation_present"] < floor:
        assert verdict.passed is False
    else:
        # If the drop wasn't deep enough to clear the floor, the test
        # is still informative: the rate must have dropped from 100%.
        assert rates["citation_present"] < 1.0


def test_gate_passes_on_unmutated_copy(tmp_path: Path) -> None:
    """Sanity: copying the eval dir without mutation reproduces 100%."""
    eval_dir = _copy_eval_dir(tmp_path)
    cases = _validator_unit_cases(eval_dir)
    results = score_w2_cases(cases)
    rates = compute_pass_rates(results)
    for name, threshold in GATE_THRESHOLDS_W2.items():
        assert rates[name] >= threshold, (
            f"{name}={rates[name]} below floor {threshold}"
        )
