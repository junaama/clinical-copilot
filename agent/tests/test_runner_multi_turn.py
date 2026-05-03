"""Multi-turn runner tests (issue 015).

Drives the runner extension that handles ``len(case.turns) > 1`` cases
end-to-end:

- A fresh in-memory ``MemorySaver`` checkpointer is constructed per case
  with a unique ``thread_id`` so conversation state threads across turns.
- Every applicable dimension (substring, citation, faithfulness,
  trajectory) is scored per-turn; the case-level dimension passes only
  when every turn's dimension passed.
- A ``multi_turn`` ``DimensionResult`` summarizes per-turn pass rate.
- Single-turn cases (``len(case.turns) == 1``) keep running through the
  pre-existing single-turn code path — backward compatibility is the
  most important property to pin down.

Tests use a stub graph injected via ``graph_factory`` so no LLM is hit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from copilot.config import Settings
from copilot.eval.case import Case, Turn
from copilot.eval.runner import run_case


def _make_settings() -> Settings:
    """Build settings without an Anthropic API key so the default judge
    factory is skipped — no LLM calls in these tests."""
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test-key",
        ANTHROPIC_API_KEY="",
        EVAL_JUDGE_MODEL="",
    )


def _make_case(
    turns: list[Turn],
    *,
    case_id: str = "test-mt",
    tier: str = "golden",
    forbidden_claims: list[str] | None = None,
    forbidden_pids: list[str] | None = None,
) -> Case:
    return Case(
        id=case_id,
        tier=tier,
        description="multi-turn test case",
        workflow="W-2",
        path=Path("/tmp/stub.yaml"),
        user_id="dr_test",
        user_role="hospitalist",
        care_team_includes=["fixture-1"],
        patient_id="fixture-1",
        conversation_id=None,
        prior_turns=[],
        turns=turns,
        expected_workflow=None,
        expected_decision="allow",
        classifier_confidence_min=None,
        forbidden_claims=forbidden_claims or [],
        forbidden_pids=forbidden_pids or [],
        citation_completeness_min=1.0,
        latency_ms_max=None,
        cost_usd_max=None,
        attack=None,
        defense_required=[],
        raw={},
    )


class _StubGraph:
    """Records every ainvoke call and returns canned per-turn responses.

    Tests pass one response per expected turn. Each response is a dict
    shaped like the real LangGraph output: ``messages`` carries the
    AIMessage(s) and ToolMessage(s) the runner extracts, ``decision``
    is the verifier's decision, ``fetched_refs`` is what the parent
    graph stashes for the runner to merge.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self.invocations: list[dict[str, Any]] = []

    async def ainvoke(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.invocations.append({"inputs": inputs, "config": dict(config)})
        if len(self.invocations) > len(self._responses):
            raise AssertionError(
                f"stub graph received {len(self.invocations)} calls but "
                f"only {len(self._responses)} canned responses provided"
            )
        return self._responses[len(self.invocations) - 1]


def _response(
    text: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    decision: str = "allow",
    fetched_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Build a graph-output dict for one turn."""
    msgs: list[Any] = []
    if tool_calls:
        msgs.append(AIMessage(content="", tool_calls=tool_calls))
        for tc in tool_calls:
            msgs.append(
                ToolMessage(
                    content='{"ok":true,"rows":[]}',
                    tool_call_id=tc.get("id", "tc"),
                )
            )
    msgs.append(AIMessage(content=text))
    return {
        "messages": msgs,
        "decision": decision,
        "fetched_refs": fetched_refs or [],
        "tool_results": [],
    }


# ---------------------------------------------------------------------------
# Backwards compat: a one-element ``turns`` list runs through the existing
# single-turn code path with no multi_turn dimension attached.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_turn_case_uses_single_turn_path() -> None:
    case = _make_case(
        [Turn(prompt="hello", must_contain=["world"])],
        case_id="mt-backcompat",
    )
    stub = _StubGraph([_response("hello world")])

    result = await run_case(
        case,
        settings=_make_settings(),
        graph_factory=lambda *a, **kw: stub,
    )

    assert len(stub.invocations) == 1
    # Single-turn cases must NOT carry a multi_turn dimension — the column
    # is meaningless for them.
    assert "multi_turn" not in result.dimensions
    assert result.passed is True


# ---------------------------------------------------------------------------
# Multi-turn: state threading via MemorySaver + thread_id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_turn_case_threads_memory_state() -> None:
    """All three turns share one ``thread_id`` so the LangGraph
    ``MemorySaver`` checkpointer accumulates conversation history."""
    case = _make_case(
        [
            Turn(prompt="t1", must_contain=["one"]),
            Turn(prompt="t2", must_contain=["two"]),
            Turn(prompt="t3", must_contain=["three"]),
        ],
        case_id="mt-thread",
    )
    stub = _StubGraph(
        [
            _response("turn one"),
            _response("turn two"),
            _response("turn three"),
        ]
    )

    result = await run_case(
        case,
        settings=_make_settings(),
        graph_factory=lambda *a, **kw: stub,
    )

    assert len(stub.invocations) == 3
    thread_ids = {
        inv["config"]["configurable"]["thread_id"] for inv in stub.invocations
    }
    assert len(thread_ids) == 1, (
        f"expected one shared thread_id across turns, got {thread_ids!r}"
    )

    # Each invocation should send only the new user message — the
    # checkpointer (not the runner) is responsible for replaying history.
    for inv in stub.invocations:
        msgs = inv["inputs"]["messages"]
        assert len(msgs) == 1, f"expected 1 message per turn, got {len(msgs)}"

    assert result.dimensions["multi_turn"].passed is True
    assert result.dimensions["multi_turn"].score == pytest.approx(1.0)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Per-turn aggregation: any-turn-fail propagates.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_2_failure_propagates_even_if_turn_3_recovers() -> None:
    case = _make_case(
        [
            Turn(prompt="t1", must_contain=["one"]),
            Turn(prompt="t2", must_contain=["two"]),
            Turn(prompt="t3", must_contain=["three"]),
        ],
        case_id="mt-anyfail",
    )
    stub = _StubGraph(
        [
            _response("turn one"),
            _response("nothing matches at all"),  # missing "two"
            _response("turn three"),
        ]
    )

    result = await run_case(
        case,
        settings=_make_settings(),
        graph_factory=lambda *a, **kw: stub,
    )

    assert result.passed is False
    # Substring dimension at the case level is the AND of per-turn substring
    # verdicts — turn 2 missed, so the case-level dim fails even though turns
    # 1 and 3 passed substring.
    assert result.dimensions["substring"].passed is False
    # Multi-turn pass rate: 2 of 3 turns passed all dimensions.
    assert result.dimensions["multi_turn"].score == pytest.approx(2 / 3)
    assert result.dimensions["multi_turn"].passed is False
    # Failure list should make clear which turn failed.
    joined = "\n".join(result.failures)
    assert "turn 2" in joined.lower() or "[t2]" in joined.lower() or "t2" in joined


# ---------------------------------------------------------------------------
# Per-dimension aggregation: trajectory across turns.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dimension_results_aggregate_correctly_across_turns() -> None:
    """Each turn declares its own ``required_tools``; the case-level
    trajectory dimension passes only if every turn's required tools were
    actually called that turn."""
    case = _make_case(
        [
            Turn(prompt="t1", required_tools=["foo"]),
            Turn(prompt="t2", required_tools=["bar"]),
        ],
        case_id="mt-trajectory",
    )
    # Turn 1 calls foo (passes its trajectory). Turn 2 calls baz (misses bar).
    stub = _StubGraph(
        [
            _response("ok", tool_calls=[{"name": "foo", "id": "1", "args": {}}]),
            _response("ok", tool_calls=[{"name": "baz", "id": "2", "args": {}}]),
        ]
    )

    result = await run_case(
        case,
        settings=_make_settings(),
        graph_factory=lambda *a, **kw: stub,
    )

    # Trajectory dim is AND of per-turn trajectory: turn 2 missed bar.
    assert result.dimensions["trajectory"].passed is False
    # multi_turn turn-pass-rate: 1 of 2.
    assert result.dimensions["multi_turn"].score == pytest.approx(0.5)
    assert result.passed is False


# ---------------------------------------------------------------------------
# Multi-turn cases never re-bind ``patient_id`` mid-conversation — the
# runner sends the same patient_id on every turn so the gate stays bound.
# Cross-patient pivot cases assert this in YAML; the unit test confirms it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_id_consistent_across_turns() -> None:
    case = _make_case(
        [
            Turn(prompt="t1"),
            Turn(prompt="t2"),
        ],
        case_id="mt-pid",
    )
    stub = _StubGraph(
        [
            _response("a"),
            _response("b"),
        ]
    )

    await run_case(
        case,
        settings=_make_settings(),
        graph_factory=lambda *a, **kw: stub,
    )

    pids = {inv["inputs"]["patient_id"] for inv in stub.invocations}
    assert pids == {"fixture-1"}


# ---------------------------------------------------------------------------
# Multi-turn pass: turn_pass_rate score, all turns clean.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_turns_pass_yields_full_score() -> None:
    case = _make_case(
        [
            Turn(prompt="t1", must_contain=["alpha"]),
            Turn(prompt="t2", must_contain=["beta"]),
        ],
        case_id="mt-allpass",
    )
    stub = _StubGraph(
        [
            _response("alpha here"),
            _response("beta here"),
        ]
    )

    result = await run_case(
        case,
        settings=_make_settings(),
        graph_factory=lambda *a, **kw: stub,
    )

    assert result.passed is True
    assert result.dimensions["multi_turn"].passed is True
    assert result.dimensions["multi_turn"].score == pytest.approx(1.0)
    # Standardized score in scores dict for langfuse flatten
    mt_details = result.dimensions["multi_turn"].details
    assert mt_details["turn_pass_rate"] == pytest.approx(1.0)
    assert mt_details["turns_total"] == 2
    assert mt_details["turns_passed"] == 2


# ---------------------------------------------------------------------------
# Forbidden-claim per-turn enforcement: a forbidden phrase appearing on
# any turn fails the case (cross-patient pivot uses this to assert no
# leak of the prior patient on the new-patient turn).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forbidden_claim_appearing_in_any_turn_fails_case() -> None:
    case = _make_case(
        [
            Turn(prompt="t1"),
            Turn(prompt="t2"),
        ],
        case_id="mt-forbidden",
        forbidden_claims=["leak"],
    )
    stub = _StubGraph(
        [
            _response("clean response one"),
            _response("oh no, a leak appeared"),  # forbidden in turn 2
        ]
    )

    result = await run_case(
        case,
        settings=_make_settings(),
        graph_factory=lambda *a, **kw: stub,
    )

    assert result.passed is False
    assert result.dimensions["forbidden"].passed is False
