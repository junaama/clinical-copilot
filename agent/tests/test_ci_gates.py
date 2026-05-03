"""Tests for tier-differentiated CI gates and ``release_blocker`` semantics.

Issue 017 — wires per-tier pass-rate gates into pytest exit codes plus a
``release_blocker: true`` field on adversarial cases. The gates are:

- Smoke: 100% to merge.
- Golden: ≥80% to release (aspirational); failure must surface the actual
  percentage, not hide it.
- Adversarial split: ``release_blocker: true`` cases must hit 100%; the
  remaining quality cases must hit ≥75%.

Tests cover the gate function in isolation against fixture
``CaseResult`` sets and the YAML loader's parse of ``release_blocker``.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from copilot.eval.case import Case, CaseResult, DimensionResult, Turn, load_case
from copilot.eval.gates import (
    GATE_THRESHOLDS,
    GateVerdict,
    evaluate_tier_gates,
    overall_exit_status,
)


def _stub_case(
    case_id: str = "c-1",
    tier: str = "smoke",
    *,
    release_blocker: bool = False,
) -> Case:
    return Case(
        id=case_id,
        tier=tier,
        description="",
        workflow="",
        path=Path("/tmp/stub.yaml"),
        user_id="u",
        user_role="hospitalist",
        care_team_includes=[],
        patient_id="fixture-1",
        conversation_id=None,
        prior_turns=[],
        turns=[Turn(prompt="stub")],
        expected_workflow=None,
        expected_decision="allow",
        classifier_confidence_min=None,
        forbidden_claims=[],
        forbidden_pids=[],
        citation_completeness_min=1.0,
        latency_ms_max=None,
        cost_usd_max=None,
        attack=None,
        defense_required=[],
        raw={},
        release_blocker=release_blocker,
    )


def _stub_result(case: Case, *, passed: bool) -> CaseResult:
    return CaseResult(
        case=case,
        passed=passed,
        response_text="",
        citations=[],
        tool_calls=[],
        decision="allow",
        latency_ms=0,
        cost_usd=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        scores={},
        failures=[] if passed else ["stub failure"],
        dimensions={"substring": DimensionResult(name="substring", passed=passed)},
    )


# ---- release_blocker schema -------------------------------------------------


def test_case_dataclass_carries_release_blocker_default_false() -> None:
    case = _stub_case()
    assert case.release_blocker is False


def test_yaml_loader_parses_release_blocker_true(tmp_path: Path) -> None:
    body = dedent(
        """
        id: adversarial-x
        tier: adversarial
        description: test
        workflow: W-2
        authenticated_as:
          user_id: u
          role: hospitalist
          care_team_includes: [fixture-1]
        session_context:
          patient_id: fixture-1
        release_blocker: true
        turns:
          - prompt: "x"
        expected:
          decision: allow
        """
    )
    path = tmp_path / "case.yaml"
    path.write_text(body)
    case = load_case(path)
    assert case.release_blocker is True


def test_yaml_loader_release_blocker_default_false_when_absent(tmp_path: Path) -> None:
    body = dedent(
        """
        id: adversarial-y
        tier: adversarial
        description: test
        workflow: W-2
        authenticated_as:
          user_id: u
          role: hospitalist
          care_team_includes: [fixture-1]
        session_context:
          patient_id: fixture-1
        turns:
          - prompt: "x"
        expected:
          decision: allow
        """
    )
    path = tmp_path / "case.yaml"
    path.write_text(body)
    case = load_case(path)
    assert case.release_blocker is False


# ---- tier thresholds --------------------------------------------------------


def test_tier_thresholds_match_prd() -> None:
    """Thresholds the PRD names: smoke 1.0, golden 0.8, adversarial blocker
    1.0, adversarial quality 0.75. Pin them so a drift in the constants
    triggers the test."""
    assert GATE_THRESHOLDS["smoke"] == 1.0
    assert GATE_THRESHOLDS["golden"] == 0.8
    assert GATE_THRESHOLDS["adversarial_blocker"] == 1.0
    assert GATE_THRESHOLDS["adversarial_quality"] == 0.75


# ---- smoke gate -------------------------------------------------------------


def test_smoke_gate_100_percent_passes() -> None:
    cases = [_stub_case(f"s{i}", "smoke") for i in range(5)]
    results = [_stub_result(c, passed=True) for c in cases]
    verdicts = evaluate_tier_gates(results)
    assert "smoke" in verdicts
    assert verdicts["smoke"].passed is True


def test_smoke_gate_one_failure_blocks_merge() -> None:
    cases = [_stub_case(f"s{i}", "smoke") for i in range(5)]
    results = [_stub_result(cases[0], passed=False)] + [
        _stub_result(c, passed=True) for c in cases[1:]
    ]
    verdicts = evaluate_tier_gates(results)
    assert verdicts["smoke"].passed is False
    # Pytest exit code must reflect the gate failure.
    assert overall_exit_status(verdicts) != 0


# ---- golden gate ------------------------------------------------------------


def test_golden_gate_at_threshold_passes() -> None:
    cases = [_stub_case(f"g{i}", "golden") for i in range(10)]
    # 8/10 = 80.0% — the threshold itself is the floor (≥, not >).
    results = [_stub_result(c, passed=True) for c in cases[:8]] + [
        _stub_result(c, passed=False) for c in cases[8:]
    ]
    verdicts = evaluate_tier_gates(results)
    assert verdicts["golden"].passed is True


def test_golden_gate_below_threshold_blocks_release_with_percentage_in_message() -> None:
    cases = [_stub_case(f"g{i}", "golden") for i in range(10)]
    # 7/10 = 70.0% — below the 80% gate.
    results = [_stub_result(c, passed=True) for c in cases[:7]] + [
        _stub_result(c, passed=False) for c in cases[7:]
    ]
    verdicts = evaluate_tier_gates(results)
    assert verdicts["golden"].passed is False
    # Surface the actual percentage — the gap must be visible, not hidden.
    summary = verdicts["golden"].summary
    assert "70.0" in summary or "70%" in summary
    assert "80" in summary  # threshold visible alongside actual


# ---- adversarial split gate -------------------------------------------------


def test_adversarial_blocker_failure_blocks_release_regardless_of_quality() -> None:
    blockers = [
        _stub_case(f"adv-blocker-{i}", "adversarial", release_blocker=True)
        for i in range(3)
    ]
    quality = [
        _stub_case(f"adv-quality-{i}", "adversarial", release_blocker=False)
        for i in range(4)
    ]
    # Quality cases all pass; blockers have one fail → release blocked.
    results = (
        [_stub_result(blockers[0], passed=False)]
        + [_stub_result(b, passed=True) for b in blockers[1:]]
        + [_stub_result(q, passed=True) for q in quality]
    )
    verdicts = evaluate_tier_gates(results)
    assert verdicts["adversarial"].passed is False
    # The blocker subset is what tripped it — message must say so.
    assert "blocker" in verdicts["adversarial"].summary.lower()
    assert overall_exit_status(verdicts) != 0


def test_adversarial_blockers_all_pass_quality_at_75_passes() -> None:
    blockers = [
        _stub_case(f"adv-blocker-{i}", "adversarial", release_blocker=True)
        for i in range(2)
    ]
    quality = [
        _stub_case(f"adv-quality-{i}", "adversarial", release_blocker=False)
        for i in range(4)
    ]
    # Blockers 2/2; quality 3/4 = 75% → both at threshold, gate passes.
    results = (
        [_stub_result(b, passed=True) for b in blockers]
        + [_stub_result(q, passed=True) for q in quality[:3]]
        + [_stub_result(quality[3], passed=False)]
    )
    verdicts = evaluate_tier_gates(results)
    assert verdicts["adversarial"].passed is True


def test_adversarial_quality_below_75_blocks_with_percentage_surfaced() -> None:
    blockers = [
        _stub_case(f"adv-blocker-{i}", "adversarial", release_blocker=True)
        for i in range(2)
    ]
    quality = [
        _stub_case(f"adv-quality-{i}", "adversarial", release_blocker=False)
        for i in range(4)
    ]
    # Blockers all pass; quality 2/4 = 50% — below the 75% gate.
    results = (
        [_stub_result(b, passed=True) for b in blockers]
        + [_stub_result(q, passed=True) for q in quality[:2]]
        + [_stub_result(q, passed=False) for q in quality[2:]]
    )
    verdicts = evaluate_tier_gates(results)
    assert verdicts["adversarial"].passed is False
    summary = verdicts["adversarial"].summary
    assert "quality" in summary.lower()
    assert "50" in summary


def test_adversarial_with_no_blockers_uses_quality_threshold_only() -> None:
    """Tier with no ``release_blocker: true`` cases reduces to the quality
    gate alone — useful sanity-check for early adversarial drafts."""
    quality = [
        _stub_case(f"adv-quality-{i}", "adversarial", release_blocker=False)
        for i in range(4)
    ]
    results = [_stub_result(q, passed=True) for q in quality]
    verdicts = evaluate_tier_gates(results)
    assert verdicts["adversarial"].passed is True


def test_adversarial_with_only_blockers_uses_blocker_gate_only() -> None:
    blockers = [
        _stub_case(f"adv-blocker-{i}", "adversarial", release_blocker=True)
        for i in range(3)
    ]
    results = [_stub_result(b, passed=True) for b in blockers]
    verdicts = evaluate_tier_gates(results)
    assert verdicts["adversarial"].passed is True


# ---- exit-status aggregation ------------------------------------------------


def test_overall_exit_status_zero_only_when_every_tier_passes() -> None:
    smoke = [_stub_case("s1", "smoke")]
    golden = [_stub_case(f"g{i}", "golden") for i in range(10)]
    adversarial = [
        _stub_case("ab", "adversarial", release_blocker=True),
        _stub_case("aq", "adversarial", release_blocker=False),
    ]

    all_pass = (
        [_stub_result(c, passed=True) for c in smoke]
        + [_stub_result(c, passed=True) for c in golden]
        + [_stub_result(c, passed=True) for c in adversarial]
    )
    assert overall_exit_status(evaluate_tier_gates(all_pass)) == 0

    # Drop one golden — 9/10 = 90% still ≥80%, golden gate still passes.
    nine_golden = (
        [_stub_result(c, passed=True) for c in smoke]
        + [_stub_result(c, passed=True) for c in golden[:9]]
        + [_stub_result(golden[9], passed=False)]
        + [_stub_result(c, passed=True) for c in adversarial]
    )
    assert overall_exit_status(evaluate_tier_gates(nine_golden)) == 0

    # Drop one smoke — gate now fails.
    smoke_fail = (
        [_stub_result(smoke[0], passed=False)]
        + [_stub_result(c, passed=True) for c in golden]
        + [_stub_result(c, passed=True) for c in adversarial]
    )
    assert overall_exit_status(evaluate_tier_gates(smoke_fail)) != 0


# ---- gate verdict shape -----------------------------------------------------


def test_gate_verdict_carries_pass_rate_and_blocker_breakdown() -> None:
    blockers = [
        _stub_case(f"adv-blocker-{i}", "adversarial", release_blocker=True)
        for i in range(2)
    ]
    quality = [
        _stub_case(f"adv-quality-{i}", "adversarial", release_blocker=False)
        for i in range(4)
    ]
    results = (
        [_stub_result(b, passed=True) for b in blockers]
        + [_stub_result(q, passed=True) for q in quality[:3]]
        + [_stub_result(quality[3], passed=False)]
    )
    verdict = evaluate_tier_gates(results)["adversarial"]
    assert isinstance(verdict, GateVerdict)
    assert verdict.tier == "adversarial"
    # blockers 2/2; quality 3/4
    assert verdict.details["blocker_passed"] == 2
    assert verdict.details["blocker_total"] == 2
    assert verdict.details["quality_passed"] == 3
    assert verdict.details["quality_total"] == 4


def test_gate_verdict_for_smoke_carries_simple_pass_rate() -> None:
    cases = [_stub_case(f"s{i}", "smoke") for i in range(4)]
    results = [_stub_result(c, passed=True) for c in cases[:3]] + [
        _stub_result(cases[3], passed=False)
    ]
    verdict = evaluate_tier_gates(results)["smoke"]
    assert verdict.passed is False
    assert verdict.details["passed"] == 3
    assert verdict.details["total"] == 4
    # Below 100% → gap visible.
    assert "75" in verdict.summary or "75.0" in verdict.summary


# ---- pytest output prefix for blocker failures ------------------------------


def test_release_blocker_failure_carries_distinct_prefix_in_failure_list() -> None:
    """``GateVerdict.blocker_failure_ids`` lists the case ids that failed
    the blocker subset — pytest_terminal_summary reads this to print a
    distinct ``[release-blocker]`` line for each one."""
    blockers = [
        _stub_case("adv-block-1", "adversarial", release_blocker=True),
        _stub_case("adv-block-2", "adversarial", release_blocker=True),
    ]
    results = [
        _stub_result(blockers[0], passed=False),
        _stub_result(blockers[1], passed=True),
    ]
    verdict = evaluate_tier_gates(results)["adversarial"]
    assert verdict.details["blocker_failure_ids"] == ["adv-block-1"]
