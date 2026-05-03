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


class _StubJudgeLLM:
    """Returns a canned verdict per (ref, claim) pair via a routing dict.

    Tests build the dict keyed by the citation ref; the stub returns the
    matching verdict JSON the judge would expect from a real Haiku call.
    Unmatched calls return supported=True so tests only have to declare
    the cases that should fail.
    """

    def __init__(
        self,
        verdicts_by_ref: dict[str, dict[str, Any]] | None = None,
        *,
        raise_on: set[str] | None = None,
    ) -> None:
        self._verdicts = verdicts_by_ref or {}
        self._raise_on = raise_on or set()
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
        # The judge sends a system + user message pair; the user message
        # carries the ref + claim. Stash for assertion.
        last = messages[-1]
        text = getattr(last, "content", "") if not isinstance(last, str) else last
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
