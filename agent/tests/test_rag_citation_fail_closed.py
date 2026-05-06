"""Verifier fail-closed for guideline / evidence answers (issue 028).

When the supervisor dispatched the evidence_retriever worker (W-EVD) and
the synthesizer comes back without ratified guideline citations on a
response that asserts clinical recommendations, the verifier must reject
the turn rather than letting an uncited clinical answer reach the user.

These tests exercise the verifier behaviour through ``build_graph`` so
the upstream contract (supervisor decision → worker output → verifier)
is what actually drives the rejection.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from copilot.supervisor.schemas import SupervisorAction, SupervisorDecision

from .test_graph_integration import (
    _citation_refs,
    _config,
    _FakeAgentScript,
    _final_message,
    _install_graph_stubs,
    _settings,
    _wd,
)


def _evidence_chunk_payload(*chunk_refs: str) -> str:
    """Build a retrieve_evidence ToolMessage payload for the given chunk refs."""
    items = ",".join(
        f'{{"guideline_ref": "{r}", "text": "stub"}}' for r in chunk_refs
    )
    return f'{{"ok": true, "chunks": [{items}]}}'


async def test_w_evd_uncited_clinical_claim_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence answer with a clinical recommendation but no guideline cite
    must not pass the verifier.

    The fix-closed path replaces the AIMessage with an evidence-gap
    refusal so the user never sees uncited medical advice.
    """
    from copilot.graph import build_graph

    # Worker fetched a guideline chunk, but the synthesizer wrote a
    # clinical recommendation (numeric A1c target with units) without a
    # ``<cite ref="guideline:..."/>`` tag.
    uncited_ai = AIMessage(
        content=(
            "ADA recommends an A1c target below 7.0% for most non-pregnant "
            "adults. Loop diuretics may help with volume control."
        )
    )
    evidence_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_evidence",
                        "args": {"query": "ADA A1c target"},
                        "id": "call-uncited",
                    }
                ],
            ),
            ToolMessage(
                content=_evidence_chunk_payload("guideline:ada-a1c-2024-1"),
                tool_call_id="call-uncited",
                name="retrieve_evidence",
            ),
            uncited_ai,
        ]
    )

    # Workers attempted three times (initial + MAX_REGENS regen attempts).
    # All return the same uncited synthesis to force the fail-closed path.
    repeat_messages = list(evidence_script.messages)
    evidence_script.messages = repeat_messages

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.93)],
        evidence_script=evidence_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="ADA A1c question.",
            ),
        ]
        # The verifier regen loop on the supervisor path needs additional
        # supervisor decisions because each regen pushes the agent_node
        # back into supervisor (we re-enter the agent path though, so
        # only one supervisor decision is needed).
        ,
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What does ADA say about A1c targets?")],
            "conversation_id": "conv-uncited-evd",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-uncited-evd"),
    )

    decision = result.get("decision")
    assert decision != "allow", (
        f"verifier must NOT allow an uncited evidence answer; got decision={decision!r}"
    )
    assert decision == "refused_unsourced", (
        f"expected refused_unsourced for uncited guideline answer; got {decision!r}"
    )

    final = _final_message(result)
    assert isinstance(final, AIMessage)
    text = (final.content or "").lower()
    # The refusal text should reference an evidence gap so the user
    # understands why no recommendation was given.
    assert any(
        phrase in text
        for phrase in (
            "evidence",
            "guideline",
            "no relevant",
            "couldn't ground",
            "couldn't find",
            "could not find",
        )
    ), f"refusal text should explain evidence gap, got: {final.content!r}"


async def test_w_evd_with_guideline_citation_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: the existing happy-path evidence response with a guideline
    citation continues to pass after the new gate is added."""
    from copilot.graph import build_graph

    cited_ai = AIMessage(
        content=(
            "ADA recommends an A1c target below 7.0% for most non-pregnant "
            'adults <cite ref="guideline:ada-a1c-2024-1" source="ADA" '
            'section="6.5"/>.'
        )
    )
    evidence_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_evidence",
                        "args": {"query": "ADA A1c"},
                        "id": "call-ok",
                    }
                ],
            ),
            ToolMessage(
                content=_evidence_chunk_payload("guideline:ada-a1c-2024-1"),
                tool_call_id="call-ok",
                name="retrieve_evidence",
            ),
            cited_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.95)],
        evidence_script=evidence_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="ADA A1c question.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What does ADA say about A1c targets?")],
            "conversation_id": "conv-cited-evd",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-cited-evd"),
    )

    assert result.get("decision") == "allow"
    final = _final_message(result)
    assert isinstance(final, AIMessage)
    cited = set(_citation_refs(final.content))
    assert "guideline:ada-a1c-2024-1" in cited


async def test_w_evd_evidence_gap_response_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence path with no citeable evidence: the synthesizer should
    explain the gap and not be punished by the new gate."""
    from copilot.graph import build_graph

    gap_ai = AIMessage(
        content=(
            "I could not find relevant guideline evidence for this "
            "question, so I cannot offer a grounded recommendation."
        )
    )
    evidence_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_evidence",
                        "args": {"query": "very obscure topic"},
                        "id": "call-empty",
                    }
                ],
            ),
            ToolMessage(
                content='{"ok": true, "chunks": []}',
                tool_call_id="call-empty",
                name="retrieve_evidence",
            ),
            gap_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.91)],
        evidence_script=evidence_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="No matching guideline chunks expected.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What do guidelines say about <obscure>?")],
            "conversation_id": "conv-gap-evd",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-gap-evd"),
    )

    # The agent honestly admits the evidence gap — verifier must allow.
    assert result.get("decision") == "allow", (
        f"evidence-gap honest refusal must pass; got decision={result.get('decision')!r}"
    )


async def test_chart_answer_without_guideline_cite_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W-1..W-11 chart answers don't take the new fail-closed gate.

    A standard FHIR chart answer cites Observation/Patient refs, never
    guideline refs. The new gate must not punish those answers.
    """
    from copilot.graph import build_graph

    chart_ai = AIMessage(
        content=(
            "Last A1c was 7.4% on 2026-04-15 "
            '<cite ref="Observation/lab-a1c-1"/>.'
        )
    )
    agent_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_recent_labs",
                        "args": {"patient_id": "Patient/p-1"},
                        "id": "call-chart",
                    }
                ],
            ),
            ToolMessage(
                content='{"ok": true, "results": [{"fhir_ref": "Observation/lab-a1c-1"}]}',
                tool_call_id="call-chart",
                name="get_recent_labs",
            ),
            chart_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-2", 0.95)],
        agent_script=agent_script,
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What was the last A1c?")],
            "conversation_id": "conv-chart-1",
            "user_id": "dr_smith",
            "patient_id": "Patient/p-1",
            "smart_access_token": "stub-token",
        },
        _config("conv-chart-1"),
    )

    assert result.get("decision") == "allow"
    final = _final_message(result)
    assert isinstance(final, AIMessage)
    cited = set(_citation_refs(final.content))
    assert "Observation/lab-a1c-1" in cited
