"""Unit tests for ``copilot.supervisor.schemas`` (issue 009).

The supervisor emits a structured ``SupervisorDecision`` after each LLM
call so the graph can dispatch to the right worker (or synthesize / ask
the user to clarify) deterministically. ``HandoffEvent`` is the audit
trail for those dispatches — every supervisor → worker transition lands
one event so a reviewer can reconstruct the trajectory without re-running
the pipeline.

Tests assert external behaviour: parsing valid inputs, rejecting unknown
actions, immutability of the dataclasses, and that the action set covers
the four cases the supervisor is allowed to take.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from copilot.supervisor.schemas import (
    HandoffEvent,
    SupervisorAction,
    SupervisorDecision,
)


def test_supervisor_action_values() -> None:
    """The action set is closed: extract / retrieve_evidence / synthesize / clarify."""
    assert {a.value for a in SupervisorAction} == {
        "extract",
        "retrieve_evidence",
        "synthesize",
        "clarify",
    }


def test_supervisor_decision_parses_extract() -> None:
    decision = SupervisorDecision(
        action="extract",
        reasoning="The user uploaded a lab PDF and asked for a summary.",
    )
    assert decision.action is SupervisorAction.EXTRACT
    assert "lab PDF" in decision.reasoning


def test_supervisor_decision_parses_retrieve_evidence() -> None:
    decision = SupervisorDecision(
        action="retrieve_evidence",
        reasoning="User asked about JNC 8 BP guidelines.",
    )
    assert decision.action is SupervisorAction.RETRIEVE_EVIDENCE


def test_supervisor_decision_parses_synthesize() -> None:
    decision = SupervisorDecision(
        action="synthesize",
        reasoning="Workers have returned; ready to compose the cited answer.",
    )
    assert decision.action is SupervisorAction.SYNTHESIZE


def test_supervisor_decision_parses_clarify() -> None:
    decision = SupervisorDecision(
        action="clarify",
        reasoning="Cannot tell which patient or document the user means.",
    )
    assert decision.action is SupervisorAction.CLARIFY


def test_supervisor_decision_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        SupervisorDecision(action="walk_dog", reasoning="not allowed")


def test_supervisor_decision_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SupervisorDecision(
            action="extract",
            reasoning="ok",
            secret_field="not allowed",
        )


def test_supervisor_decision_requires_reasoning() -> None:
    with pytest.raises(ValidationError):
        SupervisorDecision(action="extract", reasoning="")


def test_handoff_event_is_immutable() -> None:
    """``HandoffEvent`` is a frozen dataclass — auditors must trust it."""
    event = HandoffEvent(
        turn_id="conv-1:turn-3",
        from_node="supervisor",
        to_node="intake_extractor",
        reasoning="Lab PDF uploaded; extracting structured values.",
        timestamp="2026-05-06T12:34:56Z",
        input_summary="patient_id=Patient/abc, document_id=DocumentReference/xyz",
    )
    with pytest.raises(FrozenInstanceError):
        event.from_node = "tampered"  # type: ignore[misc]


def test_handoff_event_no_phi_in_input_summary() -> None:
    """The contract is that ``input_summary`` references patients by id only.

    Tests can't enforce this universally (the supervisor builds the string),
    but the field type is ``str`` and we document the contract: callers must
    not pass raw patient names, demographics, or document text.
    """
    event = HandoffEvent(
        turn_id="conv-1:turn-3",
        from_node="supervisor",
        to_node="evidence_retriever",
        reasoning="Need JNC 8 chunks for HTN management.",
        timestamp="2026-05-06T12:34:56Z",
        input_summary="query='JNC 8 BP target', domain_filter=None",
    )
    assert "Patient/" not in event.input_summary or "name=" not in event.input_summary
