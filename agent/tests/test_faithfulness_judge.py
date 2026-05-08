"""FaithfulnessJudge — issue 011 citation-anchored faithfulness.

Tests use a stub LLM client so judgments are deterministic and free. Cover:

- Citation parsing with the surrounding claim sentence.
- All citations supported -> pass with score 1.0.
- One citation unsupported -> fail with the judge reasoning surfaced.
- Malformed ``<cite>`` syntax is tolerated (no crash, just ignored).
- Response with zero citations -> pass (nothing to score).
- A citation referencing a resource the agent never fetched is judged as
  unsupported (cannot ground a claim in something we don't have).
- Stub LLM raising mid-judge yields a clean per-citation error rather than
  blowing up the whole result.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from copilot.eval.faithfulness import (
    FaithfulnessJudge,
    FaithfulnessResult,
    extract_citation_claims,
)

_SWEEP_MARKER = "UNCITED-CLAIM-SWEEP"


class _StubJudgeLLM:
    """Returns a canned verdict per (ref, claim) pair via a routing dict.

    Tests build the dict keyed by the citation ref; the stub returns the
    matching verdict JSON the judge would expect from a real Haiku call.
    Unmatched calls return supported=True so tests only have to declare
    the cases that should fail.

    Sweep calls are detected by the ``UNCITED-CLAIM-SWEEP`` marker the
    judge emits in its sweep system prompt. When detected, the stub returns
    ``sweep_response`` (defaulting to no flagged claims). ``raise_on_sweep``
    forces the sweep call to raise so the runner's error path is exercised.
    """

    def __init__(
        self,
        verdicts_by_ref: dict[str, dict[str, Any]] | None = None,
        *,
        raise_on: set[str] | None = None,
        sweep_response: dict[str, Any] | None = None,
        raise_on_sweep: bool = False,
    ) -> None:
        self._verdicts = verdicts_by_ref or {}
        self._raise_on = raise_on or set()
        self._sweep_response = sweep_response
        self._raise_on_sweep = raise_on_sweep
        self.calls: list[dict[str, Any]] = []
        self.sweep_calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
        # The judge sends a system + user message pair; the user message
        # carries the ref + claim (or the response text for a sweep call).
        system = messages[0]
        last = messages[-1]
        text = getattr(last, "content", "") if not isinstance(last, str) else last
        system_text = (
            getattr(system, "content", "") if not isinstance(system, str) else system
        )

        # Sweep calls carry a distinguishing marker in the system prompt.
        if _SWEEP_MARKER in system_text:
            self.sweep_calls.append({"prompt": text})
            if self._raise_on_sweep:
                raise RuntimeError("stub judge sweep boom")
            payload = self._sweep_response or {"uncited_claims": []}

            class _SweepReply:
                def __init__(self, content: str) -> None:
                    self.content = content

            return _SweepReply(json.dumps(payload))

        self.calls.append({"prompt": text})

        # Find which ref this call is about by simple substring scan.
        matched_ref: str | None = None
        for ref in self._verdicts:
            if ref in text:
                matched_ref = ref
                break
        if matched_ref is None:
            for ref in self._raise_on:
                if ref in text:
                    raise RuntimeError(f"stub judge boom on {ref}")
            payload = {"supported": True, "reasoning": "ok (default stub)"}
        else:
            if matched_ref in self._raise_on:
                raise RuntimeError(f"stub judge boom on {matched_ref}")
            payload = self._verdicts[matched_ref]

        class _Reply:
            def __init__(self, content: str) -> None:
                self.content = content

        return _Reply(json.dumps(payload))


def _resources() -> dict[str, dict[str, Any]]:
    return {
        "Observation/obs-bp-1": {
            "code": "Blood pressure",
            "value": "90/60",
            "effective_date": "2026-05-03T07:00:00Z",
        },
        "Observation/obs-bp-2": {
            "code": "Blood pressure",
            "value": "180/110",
            "effective_date": "2026-05-03T07:10:00Z",
        },
        "MedicationRequest/med-lisinopril": {
            "medication": "lisinopril 10 mg",
            "lifecycle_status": "active",
        },
    }


# ---------------------------------------------------------------------------
# extract_citation_claims
# ---------------------------------------------------------------------------


def test_extract_citation_claims_pulls_ref_and_surrounding_sentence() -> None:
    text = (
        "BP this morning was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "The patient is on lisinopril 10 mg <cite ref=\"MedicationRequest/med-lisinopril\"/>."
    )
    claims = extract_citation_claims(text)
    assert len(claims) == 2
    refs = [c.ref for c in claims]
    assert refs == ["Observation/obs-bp-1", "MedicationRequest/med-lisinopril"]
    # Each claim's text contains the cited fact.
    assert "90/60" in claims[0].claim
    assert "lisinopril" in claims[1].claim


def test_extract_citation_claims_ignores_malformed_syntax() -> None:
    text = (
        "BP was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "Bad cite: <cite ref=>. Another bad: <cite ref/>. "
        "Last good: pulse 72 <cite ref=\"Observation/obs-pulse\"/>."
    )
    claims = extract_citation_claims(text)
    refs = [c.ref for c in claims]
    assert refs == ["Observation/obs-bp-1", "Observation/obs-pulse"]


def test_extract_citation_claims_zero_returns_empty() -> None:
    assert extract_citation_claims("Plain answer with no citations.") == []
    assert extract_citation_claims("") == []


# ---------------------------------------------------------------------------
# FaithfulnessJudge.judge — happy path / failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_all_citations_supported_passes() -> None:
    text = (
        "BP this morning was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "The patient is on lisinopril 10 mg <cite ref=\"MedicationRequest/med-lisinopril\"/>."
    )
    stub = _StubJudgeLLM(
        {
            "Observation/obs-bp-1": {
                "supported": True,
                "reasoning": "value 90/60 matches the cited Observation",
            },
            "MedicationRequest/med-lisinopril": {
                "supported": True,
                "reasoning": "lisinopril 10mg matches active MedicationRequest",
            },
        }
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert isinstance(result, FaithfulnessResult)
    assert result.passed is True
    assert result.score == pytest.approx(1.0)
    assert result.total_citations == 2
    assert result.supported_count == 2
    assert all(v.supported for v in result.verdicts)
    assert stub.calls and len(stub.calls) == 2


@pytest.mark.asyncio
async def test_judge_one_citation_unsupported_fails_with_reasoning() -> None:
    text = (
        "BP was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "Patient is hypertensive at 200/130 <cite ref=\"Observation/obs-bp-2\"/>."
    )
    stub = _StubJudgeLLM(
        {
            "Observation/obs-bp-2": {
                "supported": False,
                "reasoning": "cited Observation reads 180/110, not 200/130",
            }
        }
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.passed is False
    assert result.score == pytest.approx(0.5)
    assert result.supported_count == 1
    assert result.total_citations == 2
    bad = [v for v in result.verdicts if not v.supported]
    assert len(bad) == 1
    assert bad[0].ref == "Observation/obs-bp-2"
    assert "180/110" in bad[0].reasoning


@pytest.mark.asyncio
async def test_judge_no_citations_passes_trivially() -> None:
    text = "Plain narrative with no citations."
    stub = _StubJudgeLLM()
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.passed is True
    assert result.score == pytest.approx(1.0)
    assert result.total_citations == 0
    assert result.verdicts == []
    assert stub.calls == []  # never invoked the LLM


@pytest.mark.asyncio
async def test_judge_malformed_citations_do_not_crash() -> None:
    text = (
        "BP 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "Garbled: <cite ref=>. <cite/> <cite ref=\"\"/>."
    )
    stub = _StubJudgeLLM(
        {"Observation/obs-bp-1": {"supported": True, "reasoning": "ok"}}
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    # Only the well-formed citation got scored.
    assert result.total_citations == 1
    assert result.passed is True


@pytest.mark.asyncio
async def test_judge_citation_to_unfetched_resource_fails() -> None:
    """Cannot ground a claim in something the agent never retrieved.

    The judge fails this citation without an LLM call — there is no
    resource to send."""
    text = "Sodium was 138 <cite ref=\"Observation/obs-na\"/>."
    stub = _StubJudgeLLM()
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.passed is False
    assert result.total_citations == 1
    assert result.supported_count == 0
    assert stub.calls == []  # short-circuit; no LLM call
    assert result.verdicts[0].ref == "Observation/obs-na"
    assert "not fetched" in result.verdicts[0].reasoning.lower()


@pytest.mark.asyncio
async def test_judge_llm_error_per_citation_does_not_blow_up() -> None:
    text = (
        "BP 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "On lisinopril <cite ref=\"MedicationRequest/med-lisinopril\"/>."
    )
    stub = _StubJudgeLLM(
        verdicts_by_ref={
            "MedicationRequest/med-lisinopril": {
                "supported": True,
                "reasoning": "ok",
            }
        },
        raise_on={"Observation/obs-bp-1"},
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    # The errored citation counts as unsupported (fail-closed) but the
    # other citation still got scored.
    assert result.passed is False
    assert result.total_citations == 2
    assert result.supported_count == 1
    bad = [v for v in result.verdicts if not v.supported]
    assert len(bad) == 1
    assert bad[0].error is not None
    assert "boom" in bad[0].error


# ---------------------------------------------------------------------------
# DimensionResult conversion (used by the runner)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_result_to_dimension_result_round_trip() -> None:
    text = "BP 90/60 <cite ref=\"Observation/obs-bp-1\"/>."
    stub = _StubJudgeLLM(
        {"Observation/obs-bp-1": {"supported": True, "reasoning": "ok"}}
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)
    result = await judge.judge(text, _resources())

    dim = result.to_dimension_result()
    assert dim.name == "faithfulness"
    assert dim.passed is True
    assert dim.score == pytest.approx(1.0)
    # details carries the per-citation verdicts so pytest output can render
    # the first few unsupported reasonings inline.
    assert "verdicts" in dim.details
    assert dim.details["citations_supported"] == pytest.approx(1.0)
    # Uncited-sweep field exists even when nothing flagged (empty list).
    assert dim.details["uncited_claims"] == []
    assert dim.details["uncited_count"] == 0


# ---------------------------------------------------------------------------
# Uncited-claim sweep (issue 012)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uncited_sweep_zero_claims_passes() -> None:
    """Sweep returns no flagged claims and all citations are supported -> pass.

    The sweep call is fired exactly once per case regardless of citation count;
    here we assert that it ran and produced an empty list.
    """
    text = (
        "BP this morning was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "Patient is on lisinopril 10 mg <cite ref=\"MedicationRequest/med-lisinopril\"/>."
    )
    stub = _StubJudgeLLM(
        {
            "Observation/obs-bp-1": {"supported": True, "reasoning": "ok"},
            "MedicationRequest/med-lisinopril": {"supported": True, "reasoning": "ok"},
        },
        sweep_response={"uncited_claims": []},
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.passed is True
    assert result.uncited_claims == []
    assert result.sweep_error is None
    assert len(stub.sweep_calls) == 1, "sweep call must fire exactly once"


@pytest.mark.asyncio
async def test_uncited_sweep_one_clinical_claim_flagged_fails() -> None:
    """An uncited clinical claim flagged by the sweep fails the case overall,
    even when every citation is supported. The flagged text is preserved on
    the result so pytest output can surface it inline."""
    text = (
        "BP was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "The patient's potassium was 5.8 mEq/L this morning."
    )
    stub = _StubJudgeLLM(
        {"Observation/obs-bp-1": {"supported": True, "reasoning": "ok"}},
        sweep_response={
            "uncited_claims": ["potassium was 5.8 mEq/L this morning"]
        },
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.passed is False
    # Citation-anchored score stays at 1.0; the failure mode is uncited claims.
    assert result.score == pytest.approx(1.0)
    assert result.supported_count == result.total_citations == 1
    assert result.uncited_claims == ["potassium was 5.8 mEq/L this morning"]


@pytest.mark.asyncio
async def test_uncited_sweep_drops_claims_from_cited_sentences() -> None:
    """Same-sentence cited claims belong to the per-citation grounding pass,
    not the uncited-claim sweep.
    """
    text = (
        "Patient is on lisinopril "
        '<cite ref="MedicationRequest/med-lisinopril"/>.'
    )
    stub = _StubJudgeLLM(
        verdicts_by_ref={
            "MedicationRequest/med-lisinopril": {
                "supported": True,
                "reasoning": "medication request documents lisinopril",
            }
        },
        sweep_response={
            "uncited_claims": ["Patient is on lisinopril"]
        },
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.uncited_claims == []
    assert result.passed is True


@pytest.mark.asyncio
async def test_uncited_sweep_drops_cited_claim_with_decimal_before_cite() -> None:
    text = (
        "Morning lisinopril was held due to hypotension and creatinine 1.8 "
        '<cite ref="MedicationRequest/med-lisinopril"/>.'
    )
    stub = _StubJudgeLLM(
        verdicts_by_ref={
            "MedicationRequest/med-lisinopril": {
                "supported": True,
                "reasoning": "medication request documents lisinopril",
            }
        },
        sweep_response={
            "uncited_claims": [
                "Morning lisinopril was held due to hypotension and creatinine 1.8"
            ]
        },
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.uncited_claims == []
    assert result.passed is True


@pytest.mark.asyncio
async def test_uncited_sweep_hedging_and_questions_not_flagged() -> None:
    """Hedging/clarification text (no clinical claim) keeps the case passing.

    The sweep prompt enumerates what counts as a clinical claim; non-claim
    sentences ("appears stable", "which patient?") should not be flagged.
    Asserting on the stub's behavior confirms the contract: empty
    ``uncited_claims`` -> pass.
    """
    text = (
        "I don't have access to overnight notes for this patient. "
        "Could you tell me which patient you mean? "
        "The chart appears stable based on what I can see."
    )
    stub = _StubJudgeLLM(sweep_response={"uncited_claims": []})
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.passed is True
    assert result.uncited_claims == []
    assert result.total_citations == 0


@pytest.mark.asyncio
async def test_uncited_sweep_combines_with_citation_failure() -> None:
    """Both failure modes can fire on the same case; both must surface.

    Pass only when citations 100% supported AND uncited list is empty.
    """
    text = (
        "BP was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "Patient also on metoprolol 25 mg BID."
    )
    stub = _StubJudgeLLM(
        {
            "Observation/obs-bp-1": {
                "supported": False,
                "reasoning": "obs reads 100/65 not 90/60",
            }
        },
        sweep_response={"uncited_claims": ["metoprolol 25 mg BID"]},
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.passed is False
    assert result.supported_count == 0
    assert result.uncited_claims == ["metoprolol 25 mg BID"]


@pytest.mark.asyncio
async def test_uncited_sweep_skipped_when_response_empty() -> None:
    """Empty/whitespace response -> trivially pass, no sweep call needed."""
    stub = _StubJudgeLLM(sweep_response={"uncited_claims": ["should not see"]})
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge("", _resources())

    assert result.passed is True
    assert result.uncited_claims == []
    assert stub.sweep_calls == []  # never fired


@pytest.mark.asyncio
async def test_uncited_sweep_llm_error_records_sweep_error_and_does_not_flag() -> None:
    """Sweep call raising -> fail-open (no flagged claims) but stash the
    error so the runner can surface the situation without breaking the case.

    Rationale: the per-citation path fails closed because we have a specific
    claim we cannot verify; the sweep path fails open because flagging
    invented claims would generate false negatives the runner can't debug.
    """
    text = (
        "BP was 90/60 <cite ref=\"Observation/obs-bp-1\"/>. "
        "Patient on lisinopril."
    )
    stub = _StubJudgeLLM(
        {"Observation/obs-bp-1": {"supported": True, "reasoning": "ok"}},
        raise_on_sweep=True,
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    # The per-citation path stays clean; the sweep error is captured on
    # the result so the runner can log it.
    assert result.uncited_claims == []
    assert result.sweep_error is not None
    assert "boom" in result.sweep_error
    # Sweep failure does not flip the case to a fail (fail-open).
    assert result.passed is True


@pytest.mark.asyncio
async def test_uncited_sweep_dimension_result_surfaces_flagged_claims() -> None:
    """``to_dimension_result`` includes uncited claims so the runner can
    render them in the pytest failure message."""
    text = "Glucose was 240 mg/dL this morning."
    stub = _StubJudgeLLM(
        sweep_response={"uncited_claims": ["glucose was 240 mg/dL this morning"]}
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)
    result = await judge.judge(text, _resources())

    dim = result.to_dimension_result()
    assert dim.passed is False
    # Score is the citation-anchored fraction (no citations in this response,
    # so it's 1.0 — the failure mode is uncited claims, not the score).
    assert dim.score == pytest.approx(1.0)
    assert dim.details["uncited_claims"] == ["glucose was 240 mg/dL this morning"]
    assert dim.details["uncited_count"] == 1


@pytest.mark.asyncio
async def test_uncited_sweep_malformed_json_treated_as_no_claims() -> None:
    """Sweep call returning non-JSON or wrong-shape JSON should not crash;
    treat as no claims flagged but record the parse failure on
    ``sweep_error`` so the runner can surface it."""

    class _BadReply:
        async def ainvoke(self, _messages: Any, **_kwargs: Any) -> Any:
            class _R:
                content = "not even close to json"

            return _R()

    judge = FaithfulnessJudge(llm_factory=lambda: _BadReply())

    result = await judge.judge("Some text", _resources())

    assert result.uncited_claims == []
    assert result.sweep_error is not None
    assert "parse" in result.sweep_error.lower() or "decode" in result.sweep_error.lower()
    assert result.passed is True


# ---------------------------------------------------------------------------
# Restatement filter (secondary deterministic filter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uncited_sweep_drops_medication_count_restating_cited_content() -> None:
    """A summary sentence counting medications that were individually cited
    should be dropped as a restatement, not flagged as uncited."""
    text = (
        "- **Furosemide** 40 mg PO twice daily "
        '<cite ref="MedicationRequest/med-furosemide"/>\n'
        "- **Lisinopril** 10 mg PO daily "
        '<cite ref="MedicationRequest/med-lisinopril"/>\n'
        "- **Metoprolol** 25 mg PO daily "
        '<cite ref="MedicationRequest/med-metoprolol"/>\n'
        "\n"
        "The patient is currently on three active medications."
    )
    resources = {
        **_resources(),
        "MedicationRequest/med-furosemide": {
            "medication": "furosemide 40 mg",
            "lifecycle_status": "active",
        },
        "MedicationRequest/med-metoprolol": {
            "medication": "metoprolol 25 mg",
            "lifecycle_status": "active",
        },
    }
    stub = _StubJudgeLLM(
        verdicts_by_ref={
            "MedicationRequest/med-furosemide": {
                "supported": True,
                "reasoning": "ok",
            },
            "MedicationRequest/med-lisinopril": {
                "supported": True,
                "reasoning": "ok",
            },
            "MedicationRequest/med-metoprolol": {
                "supported": True,
                "reasoning": "ok",
            },
        },
        sweep_response={
            "uncited_claims": [
                "The patient is currently on three active medications."
            ]
        },
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, resources)

    assert result.uncited_claims == []
    assert result.passed is True


@pytest.mark.asyncio
async def test_uncited_sweep_drops_medication_name_restatement() -> None:
    """A closing sentence that restates a medication name already cited above
    should be dropped as a restatement."""
    text = (
        "- **Furosemide** 40 mg PO twice daily "
        '<cite ref="MedicationRequest/med-furosemide"/>\n'
        "- **Lisinopril** 10 mg PO daily "
        '<cite ref="MedicationRequest/med-lisinopril"/>\n'
        "\n"
        "He is currently on Furosemide, which may be relevant given his "
        "renal status."
    )
    resources = {
        **_resources(),
        "MedicationRequest/med-furosemide": {
            "medication": "furosemide 40 mg",
            "lifecycle_status": "active",
        },
    }
    stub = _StubJudgeLLM(
        verdicts_by_ref={
            "MedicationRequest/med-furosemide": {
                "supported": True,
                "reasoning": "ok",
            },
            "MedicationRequest/med-lisinopril": {
                "supported": True,
                "reasoning": "ok",
            },
        },
        sweep_response={
            "uncited_claims": ["He is currently on Furosemide"]
        },
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, resources)

    assert result.uncited_claims == []
    assert result.passed is True


@pytest.mark.asyncio
async def test_uncited_sweep_keeps_genuinely_new_clinical_claim() -> None:
    """A claim mentioning a lab value NOT present in any cited line should
    NOT be dropped by the restatement filter."""
    text = (
        "- **Furosemide** 40 mg PO twice daily "
        '<cite ref="MedicationRequest/med-furosemide"/>\n'
        "\n"
        "The patient's potassium was 5.8 mEq/L this morning."
    )
    stub = _StubJudgeLLM(
        verdicts_by_ref={
            "MedicationRequest/med-furosemide": {
                "supported": True,
                "reasoning": "ok",
            },
        },
        sweep_response={
            "uncited_claims": [
                "potassium was 5.8 mEq/L this morning"
            ]
        },
    )
    judge = FaithfulnessJudge(llm_factory=lambda: stub)

    result = await judge.judge(text, _resources())

    assert result.uncited_claims == ["potassium was 5.8 mEq/L this morning"]
    assert result.passed is False
