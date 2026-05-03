"""Tests for the DimensionResult schema and per-tier scoreboard math.

Issue 010 — per-dimension result schema. Verifies:
- ``DimensionResult`` carries name, binary pass, optional continuous score,
  free-form details dict.
- ``CaseResult.dimensions`` aggregates dimensions and ``CaseResult.passed``
  is the AND of all dimension verdicts (plus no runtime error).
- The scoreboard renderer aggregates per-tier per-dimension pass rates from
  a list of ``CaseResult``s.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.eval.case import Case, CaseResult, DimensionResult
from copilot.eval.scoreboard import render_scoreboard, tier_dimension_table


def _stub_case(case_id: str = "smoke-001", tier: str = "smoke") -> Case:
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
        message="",
        expected_workflow=None,
        expected_decision="allow",
        classifier_confidence_min=None,
        required_facts=[],
        required_citation_refs=[],
        forbidden_claims=[],
        forbidden_pids=[],
        citation_completeness_min=1.0,
        latency_ms_max=None,
        cost_usd_max=None,
        attack=None,
        defense_required=[],
        raw={},
    )


def _stub_result(
    case: Case,
    dimensions: dict[str, DimensionResult],
    error: str | None = None,
) -> CaseResult:
    return CaseResult(
        case=case,
        passed=False,
        response_text="",
        citations=[],
        tool_calls=[],
        decision="allow",
        latency_ms=0,
        cost_usd=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        scores={},
        failures=[],
        dimensions=dimensions,
        error=error,
    )


def test_dimension_result_carries_required_fields() -> None:
    d = DimensionResult(
        name="substring",
        passed=True,
        score=1.0,
        details={"missing": []},
    )
    assert d.name == "substring"
    assert d.passed is True
    assert d.score == 1.0
    assert d.details == {"missing": []}


def test_dimension_result_score_optional() -> None:
    d = DimensionResult(name="decision", passed=False)
    assert d.score is None
    assert d.details == {}


def test_compute_passed_all_dimensions_pass() -> None:
    case = _stub_case()
    dims = {
        "substring": DimensionResult(name="substring", passed=True),
        "citation": DimensionResult(name="citation", passed=True),
    }
    result = _stub_result(case, dims)
    result.recompute_passed()
    assert result.passed is True


def test_compute_passed_one_dimension_fails() -> None:
    case = _stub_case()
    dims = {
        "substring": DimensionResult(name="substring", passed=True),
        "citation": DimensionResult(name="citation", passed=False),
    }
    result = _stub_result(case, dims)
    result.recompute_passed()
    assert result.passed is False


def test_compute_passed_runtime_error_short_circuits() -> None:
    case = _stub_case()
    dims = {
        "substring": DimensionResult(name="substring", passed=True),
    }
    result = _stub_result(case, dims, error="boom")
    result.recompute_passed()
    assert result.passed is False


def test_compute_passed_no_dimensions_means_pass() -> None:
    """A case with no scored dimensions and no error is considered passed."""
    case = _stub_case()
    result = _stub_result(case, dimensions={})
    result.recompute_passed()
    assert result.passed is True


def test_scoreboard_per_tier_per_dimension_math() -> None:
    smoke = _stub_case("s1", "smoke")
    golden = _stub_case("g1", "golden")
    results = [
        _stub_result(
            smoke,
            {
                "substring": DimensionResult(name="substring", passed=True),
                "citation": DimensionResult(name="citation", passed=True),
            },
        ),
        _stub_result(
            _stub_case("s2", "smoke"),
            {
                "substring": DimensionResult(name="substring", passed=False),
                "citation": DimensionResult(name="citation", passed=True),
            },
        ),
        _stub_result(
            golden,
            {
                "substring": DimensionResult(name="substring", passed=True),
                "citation": DimensionResult(name="citation", passed=False),
            },
        ),
    ]
    for r in results:
        r.recompute_passed()

    table = tier_dimension_table(results)
    # smoke tier: 2 cases, substring 1/2, citation 2/2
    assert table["smoke"]["count"] == 2
    assert table["smoke"]["dimensions"]["substring"] == pytest.approx(0.5)
    assert table["smoke"]["dimensions"]["citation"] == pytest.approx(1.0)
    # smoke overall: 1/2 (case s1 passed both, s2 failed substring)
    assert table["smoke"]["overall"] == pytest.approx(0.5)

    # golden tier: 1 case, substring 1/1, citation 0/1, overall 0/1
    assert table["golden"]["count"] == 1
    assert table["golden"]["dimensions"]["substring"] == pytest.approx(1.0)
    assert table["golden"]["dimensions"]["citation"] == pytest.approx(0.0)
    assert table["golden"]["overall"] == pytest.approx(0.0)


def test_scoreboard_handles_missing_dimension_per_case() -> None:
    """A case missing a dimension counts as 0/0 for that dimension on its tier
    (does not penalize the pass rate). The dimension only contributes to the
    denominator for cases that actually scored it."""
    a = _stub_case("a1", "smoke")
    b = _stub_case("a2", "smoke")
    results = [
        _stub_result(a, {"substring": DimensionResult(name="substring", passed=True)}),
        _stub_result(
            b,
            {
                "substring": DimensionResult(name="substring", passed=True),
                "citation": DimensionResult(name="citation", passed=False),
            },
        ),
    ]
    for r in results:
        r.recompute_passed()

    table = tier_dimension_table(results)
    assert table["smoke"]["dimensions"]["substring"] == pytest.approx(1.0)
    # Only b scored citation, and it failed → 0/1
    assert table["smoke"]["dimensions"]["citation"] == pytest.approx(0.0)


def test_scoreboard_renders_table_string() -> None:
    smoke = _stub_case("s1", "smoke")
    results = [
        _stub_result(
            smoke,
            {
                "substring": DimensionResult(name="substring", passed=True),
                "citation": DimensionResult(name="citation", passed=True),
            },
        ),
    ]
    for r in results:
        r.recompute_passed()

    rendered = render_scoreboard(results)
    assert "smoke" in rendered
    assert "substring" in rendered
    assert "citation" in rendered
    # 1/1 → 100%
    assert "100" in rendered


def test_scoreboard_empty_results() -> None:
    assert render_scoreboard([]).strip() != ""
    assert tier_dimension_table([]) == {}
