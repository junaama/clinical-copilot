"""Unit tests for the W2 baseline + regression detector (issue 010).

Tests assert the public contract: ``detect_regression`` correctly fails
when a rubric drops below floor or regresses more than ``MAX_BASELINE_DROP``,
and ``load_baseline`` / ``write_baseline`` round-trip cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from copilot.eval.baseline import (
    BaselineVerdict,
    CategoryVerdict,
    detect_regression,
    load_baseline,
    render_report,
    write_baseline,
)
from copilot.eval.w2_evaluators import (
    GATE_THRESHOLDS_W2,
    MAX_BASELINE_DROP,
    RUBRIC_NAMES,
)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_write_and_load_baseline_round_trips(tmp_path: Path) -> None:
    path = tmp_path / ".eval_baseline.json"
    rates = {
        "schema_valid": 0.97,
        "citation_present": 0.92,
        "factually_consistent": 0.91,
        "safe_refusal": 0.96,
        "no_phi_in_logs": 1.0,
    }
    write_baseline(path, rates, notes="test")
    loaded = load_baseline(path)
    assert loaded == rates


def test_load_baseline_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    """A fresh repo with no baseline file is a valid state — no regression check."""
    path = tmp_path / ".does-not-exist.json"
    assert load_baseline(path) == {}


def test_load_baseline_rejects_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"not_rubrics": {}}))
    with pytest.raises(ValueError, match="rubrics"):
        load_baseline(path)


def test_write_baseline_only_persists_known_rubrics(tmp_path: Path) -> None:
    """Stray rubric names in input are dropped; only ``RUBRIC_NAMES`` write."""
    path = tmp_path / "out.json"
    write_baseline(path, {"schema_valid": 0.99, "rogue_rubric": 0.5})
    raw = json.loads(path.read_text())
    assert set(raw["rubrics"].keys()) == set(RUBRIC_NAMES)
    assert raw["rubrics"]["schema_valid"] == 0.99
    assert "rogue_rubric" not in raw["rubrics"]


# ---------------------------------------------------------------------------
# Threshold + regression detection
# ---------------------------------------------------------------------------


def _passing_rates() -> dict[str, float]:
    """Rates that comfortably clear every floor and have no regression."""
    return {name: max(GATE_THRESHOLDS_W2[name], 0.99) for name in RUBRIC_NAMES}


def test_detect_regression_passes_when_all_above_floor_and_no_drop() -> None:
    current = _passing_rates()
    baseline = _passing_rates()
    verdict = detect_regression(current, baseline)
    assert verdict.passed is True
    assert all(c.passed for c in verdict.categories)
    assert verdict.failure_lines == []


def test_detect_regression_fails_when_below_absolute_floor() -> None:
    current = _passing_rates()
    current["citation_present"] = 0.85  # floor is 0.90
    baseline = _passing_rates()
    verdict = detect_regression(current, baseline)
    assert verdict.passed is False
    failed = [c for c in verdict.categories if not c.passed]
    assert len(failed) == 1
    assert failed[0].name == "citation_present"
    assert "below floor" in failed[0].reason


def test_detect_regression_fails_when_drop_exceeds_max() -> None:
    """A 10% drop is more than the 5% allowance — fail even above floor."""
    current = _passing_rates()
    current["schema_valid"] = 0.96  # above 0.95 floor
    baseline = _passing_rates()
    baseline["schema_valid"] = 0.99  # was 99%, dropped 3% — under cap
    # Now push drop over the cap
    baseline["schema_valid"] = 0.99
    current["schema_valid"] = 0.93  # 6% drop AND below floor
    verdict = detect_regression(current, baseline)
    assert verdict.passed is False


def test_detect_regression_passes_when_drop_within_allowance() -> None:
    current = _passing_rates()
    current["citation_present"] = 0.95  # baseline was 0.99, drop 4% (under 5%)
    baseline = _passing_rates()
    baseline["citation_present"] = 0.99
    verdict = detect_regression(current, baseline)
    assert verdict.passed is True


def test_detect_regression_fails_when_drop_exactly_above_max() -> None:
    """A drop strictly greater than ``max_drop`` fails. Equal-to is OK."""
    current = _passing_rates()
    current["citation_present"] = 0.93  # 6% below baseline 0.99
    baseline = _passing_rates()
    baseline["citation_present"] = 0.99
    verdict = detect_regression(current, baseline)
    assert verdict.passed is False
    failed = [c for c in verdict.categories if not c.passed]
    assert failed[0].name == "citation_present"
    assert "dropped" in failed[0].reason


def test_detect_regression_no_phi_floor_is_strict_100_pct() -> None:
    """``no_phi_in_logs`` floor is 1.0 — any leak fails the gate."""
    current = _passing_rates()
    current["no_phi_in_logs"] = 0.99
    baseline = _passing_rates()
    verdict = detect_regression(current, baseline)
    assert verdict.passed is False


def test_detect_regression_skips_drop_check_when_no_baseline() -> None:
    """A rubric absent from baseline is checked against floor only."""
    current = {
        "schema_valid": 0.96,
        "citation_present": 0.91,
        "factually_consistent": 0.91,
        "safe_refusal": 0.96,
        "no_phi_in_logs": 1.0,
    }
    baseline: dict[str, float] = {}  # fresh-baseline run
    verdict = detect_regression(current, baseline)
    assert verdict.passed is True


def test_detect_regression_returns_per_category_verdicts() -> None:
    current = _passing_rates()
    baseline = _passing_rates()
    verdict = detect_regression(current, baseline)
    names = [c.name for c in verdict.categories]
    assert names == RUBRIC_NAMES


def test_detect_regression_supports_thresholds_override() -> None:
    """Tests can pin specific thresholds without monkey-patching globals."""
    current = {name: 0.50 for name in RUBRIC_NAMES}
    baseline = current.copy()
    verdict = detect_regression(
        current,
        baseline,
        thresholds={name: 0.50 for name in RUBRIC_NAMES},
    )
    assert verdict.passed is True


def test_detect_regression_supports_max_drop_override() -> None:
    """Override max_drop to a tiny value to surface micro-regressions."""
    current = _passing_rates()
    current["schema_valid"] = 0.97
    baseline = _passing_rates()
    baseline["schema_valid"] = 0.99
    verdict = detect_regression(current, baseline, max_drop=0.01)
    assert verdict.passed is False  # 2% drop > 1% override


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_render_report_includes_per_category_lines() -> None:
    current = _passing_rates()
    baseline = _passing_rates()
    verdict = detect_regression(current, baseline)
    report = render_report(verdict)
    for name in RUBRIC_NAMES:
        assert name in report
    assert "PASSED" in report


def test_render_report_lists_failure_lines_explicitly() -> None:
    current = _passing_rates()
    current["citation_present"] = 0.5
    baseline = _passing_rates()
    verdict = detect_regression(current, baseline)
    report = render_report(verdict)
    assert "FAILED" in report
    assert "citation_present" in report
    assert "Failures:" in report


def test_max_baseline_drop_constant_pinned() -> None:
    """The 5% allowance is in the PRD — don't loosen quietly."""
    assert MAX_BASELINE_DROP == 0.05


def test_baseline_verdict_dataclass_shape() -> None:
    """Defensive: callers depend on these field names."""
    verdict = BaselineVerdict(
        passed=True, categories=[], summary="x", failure_lines=[]
    )
    assert verdict.passed is True
    assert verdict.categories == []
    assert verdict.failure_lines == []


def test_category_verdict_dataclass_shape() -> None:
    cat = CategoryVerdict(
        name="schema_valid",
        current_rate=0.95,
        baseline_rate=0.97,
        threshold=0.95,
        max_drop=0.05,
        passed=True,
        reason="ok",
    )
    assert cat.name == "schema_valid"
    assert cat.passed is True
