"""Issue 018: clarify-routing must not fire when patient context is bound.

The classifier sees only the user's message — it does not know whether
``patient_id`` (from session_context, e.g. EHR-launch) or ``focus_pid``
(resolved earlier this conversation) is already bound in state. For
single-patient questions like "What happened to this patient overnight?"
the classifier reasonably emits ``unclear`` / low confidence, which
routed every such turn into ``clarify_node`` — the eval suite caught
this as the systemic "Please provide the patient's name" failure on
``smoke-003-overnight-event``, ``smoke-005-imaging-result``, and
``golden-w2-001-eduardo-overnight``.

Fix: the post-classifier routing helper short-circuits to ``agent``
whenever the conversation already has a bound patient. The agent's
system prompt + tool surface can disambiguate intent itself; clarify
is only useful when there is no patient context at all.
"""

from __future__ import annotations

from copilot.graph import CLASSIFIER_CONFIDENCE_THRESHOLD, _route_after_classifier


def test_routes_to_clarify_when_unclear_and_no_patient_context() -> None:
    """Cold-start standalone: no patient_id, no focus_pid — clarify is right."""
    assert _route_after_classifier(
        workflow_id="unclear",
        confidence=0.0,
        patient_id=None,
        focus_pid=None,
    ) == "clarify"


def test_routes_to_clarify_on_low_confidence_with_no_patient_context() -> None:
    """Low confidence with no patient context still clarifies."""
    assert _route_after_classifier(
        workflow_id="W-2",
        confidence=CLASSIFIER_CONFIDENCE_THRESHOLD - 0.1,
        patient_id=None,
        focus_pid=None,
    ) == "clarify"


def test_routes_to_agent_when_patient_id_bound_even_if_unclear() -> None:
    """EHR-launch / single-patient session: patient_id is bound from session
    context, so the agent has enough context regardless of classifier doubt.
    """
    assert _route_after_classifier(
        workflow_id="unclear",
        confidence=0.0,
        patient_id="fixture-1",
        focus_pid=None,
    ) == "agent"


def test_routes_to_agent_when_focus_pid_bound_even_if_unclear() -> None:
    """Mid-conversation: a previous turn resolved a patient, so focus_pid is
    set. "this patient" / "his labs" questions land here.
    """
    assert _route_after_classifier(
        workflow_id="unclear",
        confidence=0.0,
        patient_id=None,
        focus_pid="fixture-1",
    ) == "agent"


def test_routes_to_agent_when_patient_bound_and_low_confidence() -> None:
    """Low confidence does not gate clarify when patient context is bound."""
    assert _route_after_classifier(
        workflow_id="W-2",
        confidence=CLASSIFIER_CONFIDENCE_THRESHOLD - 0.5,
        patient_id="fixture-1",
        focus_pid=None,
    ) == "agent"


def test_routes_to_agent_on_high_confidence_workflow() -> None:
    """High-confidence workflow always goes to agent regardless of context."""
    assert _route_after_classifier(
        workflow_id="W-1",
        confidence=0.95,
        patient_id=None,
        focus_pid=None,
    ) == "agent"


def test_routes_panel_triage_to_agent_even_below_general_threshold() -> None:
    """Panel-triage prompts have no active patient by design. Once the
    classifier has identified W-1, route to the agent so it can call the
    panel composite instead of asking for a patient name."""
    assert _route_after_classifier(
        workflow_id="W-1",
        confidence=CLASSIFIER_CONFIDENCE_THRESHOLD - 0.2,
        patient_id="",
        focus_pid="",
    ) == "agent"


def test_empty_string_patient_id_treated_as_unbound() -> None:
    """The eval runner passes ``patient_id=""`` for panel-spanning UC-1 cases
    so the gate sees "no active patient". An empty string must not falsely
    suppress clarify.
    """
    assert _route_after_classifier(
        workflow_id="unclear",
        confidence=0.0,
        patient_id="",
        focus_pid="",
    ) == "clarify"
