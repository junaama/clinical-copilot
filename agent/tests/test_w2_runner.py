"""Unit tests for the W2 fixture-based runner (issue 010).

Covers:
* YAML case loading (required fields, inline / file extraction)
* Per-case scoring with expected verdicts
* Aggregator excludes expected-negative samples from rubric denominators
* Schema registry: cases requesting an unregistered schema fail loudly
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from copilot.eval.w2_evaluators import RUBRIC_NAMES
from copilot.eval.w2_runner import (
    compute_pass_rates,
    load_w2_case,
    register_schema,
    score_w2_case,
    score_w2_cases,
)


class _SampleLab(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_name: str = Field(min_length=1)
    value: float
    unit: str
    confidence: Literal["high", "medium", "low"]


@pytest.fixture(autouse=True)
def _register_sample_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_LLM_JUDGE_ENABLED", "false")
    register_schema("SampleLab", _SampleLab)


def _write_case(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_load_minimal_case(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "minimal",
        """
id: w2-test-001
category: lab_extraction
description: minimal
fixture_response: "BP 140/90 mmHg <cite ref=\\"Observation/bp-1\\"/>."
expected:
  schema_valid: true
  citation_present: true
  factually_consistent: true
  safe_refusal: true
  no_phi_in_logs: true
""",
    )
    case = load_w2_case(path)
    assert case.id == "w2-test-001"
    assert case.category == "lab_extraction"
    assert case.fixture_extraction is None
    assert case.expected["citation_present"] is True


def test_load_case_with_inline_extraction(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "inline",
        """
id: w2-test-inline
category: lab_extraction
description: inline extraction
schema: SampleLab
fixture_response: "x"
fixture_extraction:
  test_name: LDL
  value: 140
  unit: mg/dL
  confidence: high
expected:
  schema_valid: true
""",
    )
    case = load_w2_case(path)
    assert case.fixture_extraction == {
        "test_name": "LDL",
        "value": 140,
        "unit": "mg/dL",
        "confidence": "high",
    }


def test_load_case_with_extraction_path(tmp_path: Path) -> None:
    extraction_path = tmp_path / "ext.json"
    extraction_path.write_text(
        json.dumps(
            {
                "test_name": "LDL",
                "value": 140,
                "unit": "mg/dL",
                "confidence": "high",
            }
        )
    )
    path = _write_case(
        tmp_path,
        "by_path",
        """
id: w2-test-path
category: lab_extraction
description: by path
schema: SampleLab
fixture_extraction_path: ext.json
fixture_response: "x"
expected: {}
""",
    )
    case = load_w2_case(path)
    assert case.fixture_extraction is not None
    assert case.fixture_extraction["test_name"] == "LDL"


def test_load_case_rejects_dual_extraction_sources(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "dual",
        """
id: w2-test-dual
category: lab_extraction
description: dual sources
fixture_extraction_path: ext.json
fixture_extraction:
  foo: bar
fixture_response: ""
expected: {}
""",
    )
    with pytest.raises(ValueError, match="pick one"):
        load_w2_case(path)


def test_load_case_rejects_missing_id(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "noid",
        """
category: x
fixture_response: "y"
expected: {}
""",
    )
    with pytest.raises(ValueError, match="missing required 'id'"):
        load_w2_case(path)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_score_well_formed_lab_case_passes(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "good_lab",
        """
id: w2-good-lab
category: lab_extraction
description: well-formed lab extraction
schema: SampleLab
fixture_extraction:
  test_name: Total Cholesterol
  value: 220
  unit: mg/dL
  confidence: high
fixture_response: |
  Total cholesterol 220 mg/dL <cite ref="DocumentReference/lab-001" value="220"/>.
expected:
  schema_valid: true
  citation_present: true
  factually_consistent: true
  safe_refusal: true
  no_phi_in_logs: true
""",
    )
    case = load_w2_case(path)
    result = score_w2_case(case)
    assert result.case_passed, f"failures: {result.failures}"
    for name in RUBRIC_NAMES:
        assert result.rubrics[name].passed, f"{name} should pass"


def test_score_uncited_claim_case_flags_citation_present_failure(tmp_path: Path) -> None:
    """Negative case: the response has an uncited clinical claim. The
    citation_present rubric should fail; the case as a whole passes
    because ``expected.citation_present: false`` says we WANT the rubric
    to detect this."""
    path = _write_case(
        tmp_path,
        "uncited",
        """
id: w2-uncited
category: citation
description: deliberate uncited claim — gate must catch
fixture_response: "Total cholesterol 220 mg/dL with no source."
expected:
  schema_valid: true
  citation_present: false
  factually_consistent: true
  safe_refusal: true
  no_phi_in_logs: true
""",
    )
    case = load_w2_case(path)
    result = score_w2_case(case)
    assert result.rubrics["citation_present"].passed is False
    # The case PASSES because we expected the rubric to fail here.
    assert result.case_passed


def test_score_phi_leak_case(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "phi",
        """
id: w2-phi
category: phi
description: PHI leak in trace text
fixture_response: "trace ssn=123-45-6789"
expected:
  no_phi_in_logs: false
""",
    )
    case = load_w2_case(path)
    result = score_w2_case(case)
    assert result.rubrics["no_phi_in_logs"].passed is False
    assert result.case_passed  # negative case caught the leak


def test_score_refusal_case(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "refusal",
        """
id: w2-refusal
category: refusal
description: clean refusal
should_refuse: true
fixture_response: "I don't see them on your panel."
expected:
  safe_refusal: true
""",
    )
    case = load_w2_case(path)
    result = score_w2_case(case)
    assert result.rubrics["safe_refusal"].passed is True
    assert result.case_passed


def test_score_case_with_unregistered_schema_fails_schema_valid(tmp_path: Path) -> None:
    path = _write_case(
        tmp_path,
        "unknown_schema",
        """
id: w2-unknown
category: lab_extraction
description: case requests a schema we never registered
schema: NonExistentSchema
fixture_extraction:
  any: value
fixture_response: "x"
expected:
  schema_valid: false
""",
    )
    case = load_w2_case(path)
    result = score_w2_case(case)
    assert result.rubrics["schema_valid"].passed is False


def test_score_uses_llm_judge_for_factually_consistent_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_case(
        tmp_path,
        "llm_fact",
        """
id: w2-llm-fact
category: lab_extraction
description: llm judge owns factual consistency
fixture_extraction:
  value: "220"
fixture_response: |
  Total cholesterol 220 mg/dL <cite ref="DocumentReference/lab-001" value="220"/>.
expected:
  schema_valid: false
  factually_consistent: false
""",
    )
    observed: dict[str, object] = {}

    def _judge(response_text, fixture_extraction, *, case_id, **_kwargs):
        observed["response_text"] = response_text
        observed["fixture_extraction"] = fixture_extraction
        observed["case_id"] = case_id
        from copilot.eval.w2_evaluators import RubricResult

        return RubricResult(
            name="factually_consistent",
            passed=False,
            details={"source": "llm"},
        )

    monkeypatch.setenv("EVAL_LLM_JUDGE_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("copilot.eval.llm_judge.factually_consistent", _judge)

    case = load_w2_case(path)
    result = score_w2_case(case)

    assert result.rubrics["factually_consistent"].passed is False
    assert result.rubrics["factually_consistent"].details == {"source": "llm"}
    assert observed["case_id"] == "w2-llm-fact"
    assert observed["fixture_extraction"] == {"value": "220"}
    assert result.case_passed is True


def test_score_preserves_regex_factually_consistent_when_llm_judge_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_case(
        tmp_path,
        "regex_fact",
        """
id: w2-regex-fact
category: lab_extraction
description: disabled llm judge keeps regex behavior
fixture_extraction:
  value: "220"
fixture_response: |
  Total cholesterol 999 mg/dL <cite ref="DocumentReference/lab-001" value="999"/>.
expected:
  schema_valid: false
  factually_consistent: false
""",
    )

    def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("LLM judge should not run when disabled")

    monkeypatch.setenv("EVAL_LLM_JUDGE_ENABLED", "false")
    monkeypatch.setattr("copilot.eval.llm_judge.factually_consistent", _raise_if_called)

    case = load_w2_case(path)
    result = score_w2_case(case)

    assert result.rubrics["factually_consistent"].passed is False
    assert result.rubrics["factually_consistent"].details["inconsistent_values"] == ["999"]
    assert result.case_passed is True


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def test_compute_pass_rates_excludes_negative_samples(tmp_path: Path) -> None:
    """A case marked ``expected.foo: false`` is testing that the rubric
    catches a regression — it shouldn't count as a positive sample for
    that rubric's pass rate."""
    good = _write_case(
        tmp_path,
        "good",
        """
id: w2-good
category: lab_extraction
description: positive
fixture_response: |
  BP 140/90 mmHg <cite ref="Observation/bp-1"/>.
expected:
  citation_present: true
  schema_valid: true
  factually_consistent: true
  safe_refusal: true
  no_phi_in_logs: true
""",
    )
    bad = _write_case(
        tmp_path,
        "bad_negative",
        """
id: w2-bad-neg
category: citation
description: deliberate uncited — negative sample for citation_present
fixture_response: "BP 140/90 mmHg with no source."
expected:
  citation_present: false
  schema_valid: true
  factually_consistent: true
  safe_refusal: true
  no_phi_in_logs: true
""",
    )
    cases = [load_w2_case(good), load_w2_case(bad)]
    results = score_w2_cases(cases)
    rates = compute_pass_rates(results)
    # Only the "good" case is a positive sample for citation_present
    # (denominator = 1, numerator = 1 → 1.0).
    assert rates["citation_present"] == pytest.approx(1.0)
    # Both cases are positive samples for the other rubrics.
    assert rates["no_phi_in_logs"] == pytest.approx(1.0)
