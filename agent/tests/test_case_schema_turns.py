"""Tests for the unified ``turns: [...]`` case schema.

Issue 014 — single-turn cases become a one-element ``turns`` list,
multi-turn cases (slice 015) extend the list. Each turn carries its own
``prompt``, ``must_contain``, ``must_cite``, and
``trajectory.required_tools``. The legacy single-prompt shape is
rejected with a clear migration hint.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from copilot.eval.case import Turn, load_case, load_cases_in_dir

_BASE_HEADER = dedent(
    """
    id: smoke-x-fixture
    tier: smoke
    description: fixture for schema tests
    workflow: W-2
    authenticated_as:
      user_id: u
      role: hospitalist
      care_team_includes: [fixture-1]
    session_context:
      patient_id: fixture-1
    """
).strip()


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(_BASE_HEADER + "\n" + body)
    return path


def test_loader_parses_unified_turns_shape(tmp_path: Path) -> None:
    """Single-turn case in the new shape produces a one-element ``turns``
    list with all per-turn fields nested correctly."""
    body = dedent(
        """
        turns:
          - prompt: "What happened overnight?"
            must_contain:
              - "90/60"
              - "bolus"
            must_cite:
              - "Observation/obs-bp-2"
            trajectory:
              required_tools:
                - run_per_patient_brief
        expected:
          decision: allow
        """
    )
    case = load_case(_write(tmp_path, "single.yaml", body))

    assert len(case.turns) == 1
    turn = case.turns[0]
    assert isinstance(turn, Turn)
    assert turn.prompt == "What happened overnight?"
    assert turn.must_contain == ["90/60", "bolus"]
    assert turn.must_cite == ["Observation/obs-bp-2"]
    assert turn.required_tools == ["run_per_patient_brief"]


def test_legacy_single_prompt_shape_rejected(tmp_path: Path) -> None:
    """Cases written in the pre-014 shape (top-level ``input.message`` and
    ``expected.required_facts``) raise with a migration hint pointing at
    issue 014."""
    body = dedent(
        """
        input:
          message: "What happened overnight?"
        expected:
          decision: allow
          required_facts:
            - "90/60"
        """
    )

    with pytest.raises(ValueError, match="turns"):
        load_case(_write(tmp_path, "legacy.yaml", body))


def test_legacy_field_alongside_turns_rejected(tmp_path: Path) -> None:
    """Mixing the new ``turns`` block with legacy ``expected.required_facts``
    is also rejected — half-migrated cases fail loud rather than silently
    losing fields."""
    body = dedent(
        """
        turns:
          - prompt: "What happened overnight?"
        expected:
          decision: allow
          required_facts:
            - "90/60"
        """
    )

    with pytest.raises(ValueError, match=r"turns.*coexists with legacy"):
        load_case(_write(tmp_path, "mixed.yaml", body))


def test_empty_turns_list_rejected(tmp_path: Path) -> None:
    body = dedent(
        """
        turns: []
        expected:
          decision: allow
        """
    )
    with pytest.raises(ValueError, match="non-empty"):
        load_case(_write(tmp_path, "empty.yaml", body))


def test_turn_without_prompt_rejected(tmp_path: Path) -> None:
    body = dedent(
        """
        turns:
          - must_contain: ["x"]
        expected:
          decision: allow
        """
    )
    with pytest.raises(ValueError, match="prompt"):
        load_case(_write(tmp_path, "no-prompt.yaml", body))


def test_optional_turn_fields_default_empty(tmp_path: Path) -> None:
    body = dedent(
        """
        turns:
          - prompt: hi
        expected:
          decision: allow
        """
    )
    case = load_case(_write(tmp_path, "minimal.yaml", body))

    turn = case.turns[0]
    assert turn.must_contain == []
    assert turn.must_cite == []
    assert turn.required_tools == []


def test_multi_turn_yaml_parses(tmp_path: Path) -> None:
    """Multi-turn cases in the new shape parse into a multi-element list.
    The runner currently rejects len(turns) > 1 (slice 015), but the
    loader accepts them so case-authoring can proceed in parallel."""
    body = dedent(
        """
        turns:
          - prompt: "Brief on Eduardo"
            must_contain: ["Perez"]
          - prompt: "And the cross-cover plan?"
            trajectory:
              required_tools: [run_recent_changes]
          - prompt: "Is creatinine back to baseline?"
            must_contain: ["1.8"]
            must_cite: ["Observation/obs-cr-1"]
        expected:
          decision: allow
        """
    )
    case = load_case(_write(tmp_path, "multi.yaml", body))

    assert len(case.turns) == 3
    assert case.turns[0].prompt == "Brief on Eduardo"
    assert case.turns[0].must_contain == ["Perez"]
    assert case.turns[1].required_tools == ["run_recent_changes"]
    assert case.turns[2].must_cite == ["Observation/obs-cr-1"]


def test_case_projections_read_from_turn_zero(tmp_path: Path) -> None:
    """Single-turn callers (runner, evaluators, langfuse sync) read
    ``case.message`` / ``case.required_facts`` / ``case.required_citation_refs``
    / ``case.required_tools`` — all of which project ``turns[0]`` so the
    backward-compat surface stays one line of access."""
    body = dedent(
        """
        turns:
          - prompt: "Hello"
            must_contain: ["hi"]
            must_cite: ["Patient/fixture-1"]
            trajectory:
              required_tools: [get_recent_vitals]
        expected:
          decision: allow
        """
    )
    case = load_case(_write(tmp_path, "projection.yaml", body))

    assert case.message == "Hello"
    assert case.required_facts == ["hi"]
    assert case.required_citation_refs == ["Patient/fixture-1"]
    assert case.required_tools == ["get_recent_vitals"]


def test_all_repo_cases_load_under_new_schema() -> None:
    """End-to-end migration check: every case file shipped in the repo
    loads via the new loader. Without this, a hand-edit slip during the
    migration could ship as a silent regression. Issue 015 added three
    multi-turn golden cases (live under ``evals/golden/multi_turn/``);
    they parse with ``len(turns) > 1`` while the rest stay single-turn.
    """
    evals_root = Path(__file__).resolve().parents[1] / "evals"

    smoke = load_cases_in_dir(evals_root / "smoke")
    golden = load_cases_in_dir(evals_root / "golden")
    adversarial = load_cases_in_dir(evals_root / "adversarial")

    # Issue 016 added 1 smoke (citation-syntax) + 6 adversarial (injection
    # x2, auth-escape x1, data-quality x2, negation x1) → 6 + 12.
    assert len(smoke) == 6, f"expected 6 smoke cases, got {len(smoke)}"
    # 11 single-turn golden + 3 multi-turn golden (issue 015) = 14.
    assert len(golden) == 14, f"expected 14 golden cases, got {len(golden)}"
    assert len(adversarial) == 12, f"expected 12 adversarial cases, got {len(adversarial)}"

    multi_turn_count = sum(1 for c in golden if len(c.turns) > 1)
    assert multi_turn_count == 3, (
        f"expected 3 multi-turn golden cases, got {multi_turn_count}"
    )

    for case in smoke + adversarial:
        assert len(case.turns) == 1, (
            f"{case.id}: legacy multi-turn-via-prior_turns not supported by "
            "the issue-014 single-element migration"
        )
        assert case.turns[0].prompt, f"{case.id}: empty prompt in turn 0"

    for case in golden:
        assert case.turns, f"{case.id}: empty turns list"
        for idx, turn in enumerate(case.turns):
            assert turn.prompt, f"{case.id}: empty prompt in turn {idx}"
