"""Tests for the post-classifier routing helper extended to W-DOC / W-EVD.

The existing ``test_clarify_routing.py`` covers the W1 routing matrix
(agent vs clarify). Issue 009 adds a third destination, ``supervisor``,
that always wins for W-DOC and W-EVD regardless of patient binding —
the supervisor owns the document-extraction and evidence-retrieval
workers, and the agent_node has no tools to handle either workflow.
"""

from __future__ import annotations

from copilot.graph import _route_after_classifier


def test_w_doc_routes_to_supervisor_with_no_patient_context() -> None:
    """Document intent always routes to supervisor — even with no focus."""
    assert (
        _route_after_classifier(
            workflow_id="W-DOC",
            confidence=0.9,
            patient_id=None,
            focus_pid=None,
        )
        == "supervisor"
    )


def test_w_doc_routes_to_supervisor_with_bound_patient() -> None:
    """W-DOC overrides the bound-patient short-circuit to ``agent``.

    A bound patient does not change the dispatch; the supervisor's
    intake_extractor is the only path with document tools. Without this
    rule, "[system] Document uploaded:" sentinels emitted while a
    patient is in focus would be answered by the W1 agent, which has no
    extract_document tool.
    """
    assert (
        _route_after_classifier(
            workflow_id="W-DOC",
            confidence=0.95,
            patient_id="Patient/123",
            focus_pid=None,
        )
        == "supervisor"
    )


def test_w_evd_routes_to_supervisor_regardless_of_focus() -> None:
    """Evidence intent always goes to the supervisor."""
    assert (
        _route_after_classifier(
            workflow_id="W-EVD",
            confidence=0.85,
            patient_id=None,
            focus_pid="Patient/abc",
        )
        == "supervisor"
    )


def test_w1_through_w11_unchanged_by_supervisor_addition() -> None:
    """Adding the supervisor route must not regress W1 routing."""
    # Previously-tested W1 paths still hold:
    assert (
        _route_after_classifier(
            workflow_id="W-2",
            confidence=0.9,
            patient_id="Patient/123",
            focus_pid=None,
        )
        == "agent"
    )
    assert (
        _route_after_classifier(
            workflow_id="unclear",
            confidence=0.0,
            patient_id=None,
            focus_pid=None,
        )
        == "clarify"
    )
