"""Tests for ``copilot.supervisor.graph`` (issue 009).

The supervisor node is exercised with a fake structured-output model:
the real LangChain wrapper would call out to OpenAI / Anthropic, but
all the routing-relevant logic lives in the post-LLM dispatch and the
HandoffEvent construction. By injecting a stub model that returns a
canned ``SupervisorDecision`` we can pin the contract:

* ``supervisor_action`` and ``supervisor_reasoning`` are set in state.
* A ``HandoffEvent`` is produced with the right ``from_node`` /
  ``to_node`` / ``input_summary``.
* ``route_after_supervisor`` translates the action into the right node.
* The iteration cap forces ``synthesize`` after
  ``MAX_SUPERVISOR_ITERATIONS`` so a stuck LLM cannot infinite-loop.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from copilot.supervisor.graph import (
    MAX_SUPERVISOR_ITERATIONS,
    build_supervisor_node,
    route_after_supervisor,
)
from copilot.supervisor.schemas import SupervisorAction, SupervisorDecision


class _StubStructuredModel:
    """Stand-in for ``chat_model.with_structured_output(SupervisorDecision)``.

    ``ainvoke`` returns whatever ``decision`` was injected, mimicking the
    real model's contract without the API call. Includes an optional
    ``raise_with`` to simulate transient failures.
    """

    def __init__(self, decision: SupervisorDecision | None = None, raise_with: Exception | None = None) -> None:
        self._decision = decision
        self._raise_with = raise_with
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> SupervisorDecision:
        self.calls.append(messages)
        if self._raise_with is not None:
            raise self._raise_with
        if self._decision is None:
            raise AssertionError("stub has no decision configured")
        return self._decision


class _StubChatModel:
    """Minimal model surface so ``with_structured_output`` returns the stub."""

    def __init__(self, structured: _StubStructuredModel) -> None:
        self._structured = structured

    def with_structured_output(self, schema: Any):  # noqa: ANN401 — match real surface
        return self._structured


def _state(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [HumanMessage(content="Analyze the lab I just uploaded.")],
        "conversation_id": "conv-1",
        "workflow_id": "W-DOC",
    }
    base.update(kwargs)
    return base


async def test_supervisor_dispatches_to_intake_extractor_on_extract() -> None:
    structured = _StubStructuredModel(
        decision=SupervisorDecision(
            action=SupervisorAction.EXTRACT,
            reasoning="User uploaded a lab PDF — extract structured values.",
        )
    )
    node = build_supervisor_node(_StubChatModel(structured))

    out = await node(_state())

    assert out["supervisor_action"] == "extract"
    assert "Extract" in out["supervisor_reasoning"] or "extract" in out["supervisor_reasoning"]
    events = out["handoff_events"]
    assert len(events) == 1
    assert events[0]["from_node"] == "supervisor"
    assert events[0]["to_node"] == "intake_extractor"
    assert events[0]["turn_id"].startswith("conv-1:turn-")


async def test_supervisor_dispatches_to_evidence_retriever_on_retrieve() -> None:
    structured = _StubStructuredModel(
        decision=SupervisorDecision(
            action=SupervisorAction.RETRIEVE_EVIDENCE,
            reasoning="User asked about JNC 8 BP guidance.",
        )
    )
    node = build_supervisor_node(_StubChatModel(structured))

    out = await node(_state(workflow_id="W-EVD"))

    assert out["supervisor_action"] == "retrieve_evidence"
    assert out["handoff_events"][0]["to_node"] == "evidence_retriever"


async def test_supervisor_synthesize_routes_to_verifier() -> None:
    structured = _StubStructuredModel(
        decision=SupervisorDecision(
            action=SupervisorAction.SYNTHESIZE,
            reasoning="Workers returned; ready to compose answer.",
        )
    )
    node = build_supervisor_node(_StubChatModel(structured))

    out = await node(_state())

    assert out["supervisor_action"] == "synthesize"
    assert out["handoff_events"][0]["to_node"] == "verifier"


async def test_supervisor_clarify_action_routes_to_clarify_node() -> None:
    structured = _StubStructuredModel(
        decision=SupervisorDecision(
            action=SupervisorAction.CLARIFY,
            reasoning="User asked about a document but did not name one.",
        )
    )
    node = build_supervisor_node(_StubChatModel(structured))

    out = await node(_state())

    assert out["supervisor_action"] == "clarify"
    assert out["handoff_events"][0]["to_node"] == "clarify"


async def test_supervisor_input_summary_excludes_patient_names() -> None:
    """Audit contract: input_summary must reference patient by id only."""
    structured = _StubStructuredModel(
        decision=SupervisorDecision(
            action=SupervisorAction.EXTRACT,
            reasoning="Lab uploaded.",
        )
    )
    node = build_supervisor_node(_StubChatModel(structured))

    out = await node(_state(focus_pid="Patient/abc-123"))
    summary = out["handoff_events"][0]["input_summary"]
    assert "patient_id=Patient/abc-123" in summary
    assert "Hayes" not in summary  # no patient names — id only


async def test_supervisor_failure_falls_back_to_clarify() -> None:
    structured = _StubStructuredModel(raise_with=RuntimeError("API down"))
    node = build_supervisor_node(_StubChatModel(structured))

    out = await node(_state())

    assert out["supervisor_action"] == "clarify"
    assert "failed" in out["supervisor_reasoning"].lower()


async def test_supervisor_iteration_cap_forces_synthesize() -> None:
    """When the supervisor has run many times, force synthesize to break loops."""
    structured = _StubStructuredModel(
        decision=SupervisorDecision(
            action=SupervisorAction.EXTRACT,  # would loop forever if respected
            reasoning="Keep extracting.",
        )
    )
    node = build_supervisor_node(_StubChatModel(structured))

    out = await node(_state(supervisor_iterations=MAX_SUPERVISOR_ITERATIONS))

    assert out["supervisor_action"] == "synthesize"
    assert "iteration cap" in out["supervisor_reasoning"].lower()
    # Stub LLM was NOT called — the cap short-circuits before the API hit.
    assert structured.calls == []


def test_route_after_supervisor_handles_each_action() -> None:
    """The conditional-edge function maps action → node label."""
    assert (
        route_after_supervisor({"supervisor_action": "extract"})
        == "intake_extractor"
    )
    assert (
        route_after_supervisor({"supervisor_action": "retrieve_evidence"})
        == "evidence_retriever"
    )
    assert (
        route_after_supervisor({"supervisor_action": "synthesize"})
        == "verifier"
    )
    assert route_after_supervisor({"supervisor_action": "clarify"}) == "clarify"
    # Missing action defaults to clarify (defensive).
    assert route_after_supervisor({}) == "clarify"
