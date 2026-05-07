"""Verifier fail-closed for panel triage failures (issue 042).

When a W-1 turn dispatches ``run_panel_triage`` (or ``run_panel_med_safety``)
and the tool returns ``ok: false`` — auth failure, FHIR transport error,
HTTP 4xx/5xx surfacing through the gate — the verifier must produce a
panel-data-unavailable refusal regardless of what the synthesizer wrote.

The verifier also scrubs internal-only markers (probe names, raw error
tokens, HTTP statuses) from the final answer when the path is W-1. These
technical details belong in traces and the technical-details affordance,
not in the clinical answer.

The route metadata for a failed panel turn keeps ``kind: panel`` so the UI
badge advertises the panel route the clinician asked for, with a
``Panel data unavailable`` label naming the failure state. (Distinct from
the W-EVD failure path, which transitions to ``kind: refusal``.)
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .test_graph_integration import (
    _config,
    _FakeAgentScript,
    _final_message,
    _install_graph_stubs,
    _settings,
    _wd,
)


def _panel_triage_failure_payload(error: str) -> str:
    """Build a run_panel_triage ToolMessage payload with ok:false."""
    return (
        '{"ok": false, "rows": [], "sources_checked": ["CareTeam (panel)"], '
        f'"error": "{error}", "latency_ms": 12}}'
    )


def _panel_triage_success_payload() -> str:
    """Build a run_panel_triage ToolMessage payload with ok:true and one row."""
    return (
        '{"ok": true, "rows": ['
        '{"resource_type": "Patient", "fhir_ref": "Patient/fixture-1", '
        '"fields": {"given_name": "Eduardo", "family_name": "Perez"}}'
        '], "sources_checked": ["CareTeam (panel)"], "error": null, "latency_ms": 25}'
    )


# ---------------------------------------------------------------------------
# Predicate unit tests — _is_panel_path / _panel_triage_failed.
# ---------------------------------------------------------------------------


def test_is_panel_path_recognizes_w1_workflow_id() -> None:
    from copilot.graph import _is_panel_path

    assert _is_panel_path({"workflow_id": "W-1"}) is True
    assert _is_panel_path({"workflow_id": "W-2"}) is False
    assert _is_panel_path({}) is False


def test_is_panel_path_recognizes_panel_tool_message() -> None:
    """A run_panel_triage ToolMessage on the turn is enough by itself.

    Defense in depth: a misrouted W-2 turn that still invokes
    ``run_panel_triage`` should still get the panel-failure contract.
    """
    from copilot.graph import _is_panel_path

    state = {
        "workflow_id": "W-2",
        "messages": [
            ToolMessage(
                content="{}",
                tool_call_id="x",
                name="run_panel_triage",
            )
        ],
    }
    assert _is_panel_path(state) is True


def test_panel_triage_failed_detects_ok_false_payload() -> None:
    from copilot.graph import _panel_triage_failed

    state = {
        "messages": [
            ToolMessage(
                content=_panel_triage_failure_payload("careteam_denied"),
                tool_call_id="c1",
                name="run_panel_triage",
            )
        ]
    }
    assert _panel_triage_failed(state) is True


def test_panel_triage_failed_returns_false_for_ok_true() -> None:
    from copilot.graph import _panel_triage_failed

    state = {
        "messages": [
            ToolMessage(
                content=_panel_triage_success_payload(),
                tool_call_id="c1",
                name="run_panel_triage",
            )
        ]
    }
    assert _panel_triage_failed(state) is False


def test_panel_triage_failed_recognizes_med_safety_too() -> None:
    """``run_panel_med_safety`` is the other panel-level tool and must be
    treated the same way."""
    from copilot.graph import _panel_triage_failed

    state = {
        "messages": [
            ToolMessage(
                content='{"ok": false, "error": "careteam_denied"}',
                tool_call_id="c1",
                name="run_panel_med_safety",
            )
        ]
    }
    assert _panel_triage_failed(state) is True


# ---------------------------------------------------------------------------
# Verifier behavior — panel triage failure path.
# ---------------------------------------------------------------------------


async def test_panel_triage_tool_failure_produces_unavailable_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_panel_triage ok:false + LLM clinical ranking → panel-unavailable refusal.

    Even when the LLM produces a confident-looking ranking, the verifier
    must reject the turn and replace the answer with the panel-unavailable
    copy. The turn must NOT be reported as ``decision=allow``.
    """
    from copilot.graph import build_graph

    fallback_ai = AIMessage(
        content=(
            "Patient A is the highest priority — NEWS2 +3 since 22:00 "
            "<cite ref=\"Patient/fixture-1\"/>."
        )
    )
    agent_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_panel_triage",
                        "args": {},
                        "id": "call-fail-1",
                    }
                ],
            ),
            ToolMessage(
                content=_panel_triage_failure_payload("careteam_denied"),
                tool_call_id="call-fail-1",
                name="run_panel_triage",
            ),
            fallback_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-1", 0.93)],
        agent_script=agent_script,
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="Who needs attention first?")],
            "conversation_id": "conv-panel-fail-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-panel-fail-1"),
    )

    assert result.get("decision") == "tool_failure", (
        f"failed panel triage must refuse-closed; got {result.get('decision')!r}"
    )

    final = _final_message(result)
    assert isinstance(final, AIMessage)
    text = (final.content or "").lower()
    # Product copy.
    assert "panel" in text and "unavailable" in text
    # No internal leak markers.
    for marker in (
        "careteam_denied",
        "denied_authz",
        "run_panel_triage",
        "list_panel",
        "no_active_user",
        "tool_failure",
        "http_401",
        "http_403",
        "http_500",
    ):
        assert marker not in text, (
            f"refusal must not leak {marker!r}; got: {final.content!r}"
        )


async def test_panel_route_with_internal_leak_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W-1 + LLM repeats internal jargon → scrubbed panel-unavailable refusal.

    Even when the underlying tool succeeded, the verifier must rewrite the
    answer if the synthesizer "honestly" passes through probe names or HTTP
    statuses, so the clinician never sees those internals.
    """
    from copilot.graph import build_graph

    leaky_ai = AIMessage(
        content=(
            "I tried run_panel_triage but the careteam_denied error "
            "(HTTP 403) prevented me from ranking patients."
        )
    )
    agent_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_panel_triage",
                        "args": {},
                        "id": "call-leak-1",
                    }
                ],
            ),
            ToolMessage(
                content=_panel_triage_success_payload(),
                tool_call_id="call-leak-1",
                name="run_panel_triage",
            ),
            leaky_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-1", 0.92)],
        agent_script=agent_script,
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="Who needs attention first?")],
            "conversation_id": "conv-panel-leak-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-panel-leak-1"),
    )

    assert result.get("decision") == "tool_failure"
    final = _final_message(result)
    assert isinstance(final, AIMessage)
    text = (final.content or "").lower()
    # All internals scrubbed.
    for marker in (
        "run_panel_triage",
        "careteam_denied",
        "http 403",
        "http_403",
    ):
        assert marker not in text, (
            f"refusal must not leak {marker!r}; got: {final.content!r}"
        )
    # Still names the panel in user-facing terms.
    assert "panel" in text


async def test_panel_triage_success_still_routes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ok:true + clean LLM answer is the existing happy path; not a failure.

    Make sure the new failure-closed gate does NOT regress the existing
    behavior where the panel tool succeeded and the LLM produced a clean
    ranking with cited Patient refs.
    """
    from copilot.graph import build_graph

    happy_ai = AIMessage(
        content=(
            "Eduardo Perez is the top priority on your panel today "
            "<cite ref=\"Patient/fixture-1\"/>."
        )
    )
    agent_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_panel_triage",
                        "args": {},
                        "id": "call-ok-1",
                    }
                ],
            ),
            ToolMessage(
                content=_panel_triage_success_payload(),
                tool_call_id="call-ok-1",
                name="run_panel_triage",
            ),
            happy_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-1", 0.94)],
        agent_script=agent_script,
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="Who needs attention first?")],
            "conversation_id": "conv-panel-ok-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-panel-ok-1"),
    )

    # ok:true + cited answer still passes.
    assert result.get("decision") == "allow"
    final = _final_message(result)
    assert isinstance(final, AIMessage)


# ---------------------------------------------------------------------------
# Route metadata pins — derive_route_metadata.
# ---------------------------------------------------------------------------


def test_panel_failure_route_metadata_is_panel_unavailable() -> None:
    """A panel triage failure surfaces as ``kind: panel`` with a
    ``Panel data unavailable`` label.

    The clinician asked about the panel, the system tried the panel route
    and failed closed; the route badge stays advertised as ``panel`` so
    the UI's panel-route styling is correct, with a label that names the
    failure state. Distinct from W-EVD failures, which transition to
    ``kind: refusal``.
    """
    from copilot.api.schemas import derive_route_metadata

    route = derive_route_metadata(
        workflow_id="W-1",
        decision="tool_failure",
        supervisor_action=None,
    )
    assert route.kind == "panel"
    assert route.label == "Panel data unavailable"
    # Must NOT be reported as the chart route (the most damaging mislabel
    # — clinician would think this was a per-patient chart read).
    assert route.kind != "chart"
    # Must NOT be the success label.
    assert route.label != "Reviewing your panel"


def test_panel_success_route_metadata_is_panel_label() -> None:
    """Sanity: a successful W-1 turn still reports ``Reviewing your panel``."""
    from copilot.api.schemas import derive_route_metadata

    route = derive_route_metadata(
        workflow_id="W-1",
        decision="allow",
        supervisor_action=None,
    )
    assert route.kind == "panel"
    assert route.label == "Reviewing your panel"


def test_panel_refusal_unsourced_also_routes_panel_unavailable() -> None:
    """W-1 + refused_unsourced (e.g. LLM hallucinated cohort patients) is
    also a panel failure from the user's POV — the panel data couldn't be
    surfaced. The label maps to ``Panel data unavailable`` so the clinician
    sees one consistent failure copy regardless of the underlying cause."""
    from copilot.api.schemas import derive_route_metadata

    route = derive_route_metadata(
        workflow_id="W-1",
        decision="refused_unsourced",
        supervisor_action=None,
    )
    assert route.kind == "panel"
    assert route.label == "Panel data unavailable"


def test_non_w1_refusal_routes_unchanged() -> None:
    """A W-EVD or W-DOC refusal must still surface as ``kind: refusal``.

    Issue 042 only changes the W-1 branch; the existing guideline /
    document failure paths must be unaffected.
    """
    from copilot.api.schemas import derive_route_metadata

    evd = derive_route_metadata(
        workflow_id="W-EVD",
        decision="refused_unsourced",
        supervisor_action="retrieve_evidence",
    )
    assert evd.kind == "refusal"

    doc = derive_route_metadata(
        workflow_id="W-DOC",
        decision="refused_unsourced",
        supervisor_action="extract",
    )
    assert doc.kind == "refusal"


# ---------------------------------------------------------------------------
# /chat envelope diagnostics — issue 042 technical-details affordance.
# ---------------------------------------------------------------------------


def test_chat_envelope_carries_diagnostics_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /chat response state includes ``diagnostics`` with decision +
    supervisor_action so the UI can render them inside a hidden-by-default
    Technical details affordance.

    The clinical answer must NOT include these values — the verifier scrubs
    them — but a developer or grader needs to see them off-stage to confirm
    the system failed closed rather than hallucinated.
    """
    from contextlib import asynccontextmanager
    from typing import Any

    from fastapi.testclient import TestClient
    from langchain_core.messages import AIMessage as _AIMessage

    from copilot import server
    from copilot.api.schemas import PlainBlock

    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    @asynccontextmanager
    async def _stub_open_checkpointer(_settings: Any):
        yield None

    class _DiagGraph:
        async def ainvoke(
            self, inputs: dict[str, Any], config: dict[str, Any]
        ) -> dict[str, Any]:
            block = PlainBlock(
                lead=(
                    "Panel data is unavailable right now, so I can't rank "
                    "the patients on your panel."
                )
            )
            return {
                "messages": [_AIMessage(content=block.lead)],
                "patient_id": inputs.get("patient_id"),
                "workflow_id": "W-1",
                "classifier_confidence": 0.9,
                "decision": "tool_failure",
                "supervisor_action": "",
                "block": block.model_dump(by_alias=True),
            }

    monkeypatch.setattr(server, "build_graph", lambda *_a, **_kw: _DiagGraph())
    monkeypatch.setattr(server, "open_checkpointer", _stub_open_checkpointer)

    with TestClient(server.app) as client:
        server.app.state.graph = _DiagGraph()
        response = client.post(
            "/chat",
            json={
                "conversation_id": "demo-diag",
                "patient_id": "fixture-1",
                "user_id": "dr_smith",
                "smart_access_token": "stub",
                "message": "Who needs attention first?",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    diagnostics = body["state"]["diagnostics"]
    assert diagnostics["decision"] == "tool_failure"
    assert diagnostics["supervisor_action"] == ""
    # Route still advertises panel even on failure (issue 042 contract).
    route = body["state"]["route"]
    assert route["kind"] == "panel"
    assert route["label"] == "Panel data unavailable"
    # The clinical answer must not leak the raw decision token.
    assert "tool_failure" not in body["reply"]
