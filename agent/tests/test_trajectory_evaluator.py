"""Tests for the TrajectoryEvaluator.

Issue 013 — trajectory dimension. The evaluator is a pure function:
given the list of tool-call records the LangGraph agent produced and the
list of ``required_tools`` declared in the case YAML, return a structured
result with missing required tools, present-but-not-required tools, and a
binary pass field. Set-membership only — no ordering, no argument
matching, no forbidden-tools list.
"""

from __future__ import annotations

from copilot.eval.trajectory import TrajectoryResult, evaluate_trajectory


def _call(name: str) -> dict[str, object]:
    return {"name": name, "args": {}, "id": f"call-{name}"}


def test_required_all_present_passes() -> None:
    tool_calls = [_call("get_recent_vitals"), _call("get_active_medications")]
    required = ["get_recent_vitals", "get_active_medications"]

    result = evaluate_trajectory(tool_calls, required)

    assert isinstance(result, TrajectoryResult)
    assert result.passed is True
    assert result.missing == []
    assert result.required_tools == ["get_recent_vitals", "get_active_medications"]


def test_one_required_missing_fails_with_tool_named() -> None:
    tool_calls = [_call("get_recent_vitals")]
    required = ["get_recent_vitals", "get_active_medications"]

    result = evaluate_trajectory(tool_calls, required)

    assert result.passed is False
    assert result.missing == ["get_active_medications"]


def test_extra_tools_allowed_passes() -> None:
    """Set-membership semantics: required is a subset, extras are fine."""
    tool_calls = [
        _call("get_recent_vitals"),
        _call("get_active_medications"),
        _call("get_recent_labs"),  # extra, not in required
    ]
    required = ["get_recent_vitals", "get_active_medications"]

    result = evaluate_trajectory(tool_calls, required)

    assert result.passed is True
    assert result.missing == []
    assert "get_recent_labs" in result.extra


def test_empty_required_passes_regardless() -> None:
    """Cases that don't care about trajectory (empty required_tools) always
    pass — the dimension never fails on empty required."""
    assert evaluate_trajectory([], []).passed is True
    assert evaluate_trajectory([_call("anything")], []).passed is True
    assert evaluate_trajectory([_call("a"), _call("b"), _call("c")], []).passed is True


def test_duplicate_required_calls_count_once() -> None:
    """Required tool called multiple times still satisfies the requirement
    (set-membership, not multiset)."""
    tool_calls = [_call("get_recent_vitals"), _call("get_recent_vitals")]
    required = ["get_recent_vitals"]

    result = evaluate_trajectory(tool_calls, required)

    assert result.passed is True
    assert result.missing == []


def test_missing_preserves_required_order() -> None:
    """``missing`` should list missing tools in the order they were declared
    in the case YAML so failure messages are predictable."""
    tool_calls: list[dict[str, object]] = []
    required = ["get_a", "get_b", "get_c"]

    result = evaluate_trajectory(tool_calls, required)

    assert result.missing == ["get_a", "get_b", "get_c"]


def test_tool_call_records_with_missing_name_skipped() -> None:
    """Tool-call records without a ``name`` (malformed) don't crash and don't
    contribute to either side of the verdict."""
    tool_calls = [{"args": {}}, _call("get_recent_vitals")]
    required = ["get_recent_vitals"]

    result = evaluate_trajectory(tool_calls, required)

    assert result.passed is True


def test_to_dimension_result_round_trip() -> None:
    """``to_dimension_result`` produces a DimensionResult with name='trajectory'
    and details carrying missing/extra/required for downstream rendering."""
    tool_calls = [_call("a"), _call("c")]
    required = ["a", "b"]

    result = evaluate_trajectory(tool_calls, required)
    dim = result.to_dimension_result()

    assert dim.name == "trajectory"
    assert dim.passed is False
    assert dim.score == 0.5  # 1 of 2 required present
    assert dim.details["missing"] == ["b"]
    assert dim.details["required"] == ["a", "b"]
    assert "c" in dim.details["extra"]


def test_to_dimension_result_empty_required_score_one() -> None:
    """Empty required → score 1.0 (no false negatives)."""
    result = evaluate_trajectory([_call("a")], [])
    dim = result.to_dimension_result()

    assert dim.name == "trajectory"
    assert dim.passed is True
    assert dim.score == 1.0
    assert dim.details["required"] == []


def test_case_loader_reads_required_tools_from_turn_trajectory() -> None:
    """The case loader picks up ``turns[i].trajectory.required_tools`` and
    exposes it via ``Case.required_tools`` (single-turn projection) so the
    runner can pass it to the evaluator without poking at the turns list
    at call sites."""
    from pathlib import Path
    from textwrap import dedent

    from copilot.eval.case import load_case

    payload = dedent(
        """
        id: smoke-x-trajectory-loader
        tier: smoke
        description: trajectory loader fixture
        workflow: W-2
        authenticated_as:
          user_id: u
          role: hospitalist
          care_team_includes: [fixture-1]
        session_context:
          patient_id: fixture-1
        turns:
          - prompt: hi
            trajectory:
              required_tools:
                - get_recent_vitals
                - get_active_medications
        expected:
          decision: allow
        """
    ).strip()

    tmp = Path("/tmp/case_loader_required_tools.yaml")
    tmp.write_text(payload)
    case = load_case(tmp)
    assert case.required_tools == ["get_recent_vitals", "get_active_medications"]
    assert case.turns[0].required_tools == [
        "get_recent_vitals",
        "get_active_medications",
    ]


def test_case_loader_required_tools_default_empty() -> None:
    """Cases without ``required_tools`` get an empty list — never crashes."""
    from pathlib import Path
    from textwrap import dedent

    from copilot.eval.case import load_case

    payload = dedent(
        """
        id: smoke-x-trajectory-default
        tier: smoke
        description: default empty
        workflow: W-2
        authenticated_as:
          user_id: u
          role: hospitalist
          care_team_includes: []
        session_context:
          patient_id: fixture-1
        turns:
          - prompt: hi
        expected:
          decision: allow
        """
    ).strip()

    tmp = Path("/tmp/case_loader_required_tools_default.yaml")
    tmp.write_text(payload)
    case = load_case(tmp)
    assert case.required_tools == []
    assert case.turns[0].required_tools == []
