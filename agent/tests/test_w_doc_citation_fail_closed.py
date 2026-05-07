"""Verifier fail-closed for document / W-DOC answers (issue 035).

When the supervisor dispatched the intake_extractor worker (W-DOC) and
the synthesizer comes back with clinical claims that carry no citation
at all, the verifier must reject the turn rather than letting an
uncited document-derived clinical claim reach the user.

Mirrors the W-EVD fail-closed gate in
``test_rag_citation_fail_closed.py`` (issue 028) but applied to the
document path so an extracted lab value cannot be presented as chart
truth without provenance.
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


def _document_extract_payload(document_id: str) -> str:
    """Build an extract_document ToolMessage payload with a real doc id."""
    return (
        '{"ok": true, "document_ref": "DocumentReference/'
        + document_id
        + '", "results": [{"test_name": "LDL", "value": "180", "unit": '
        '"mg/dL", "confidence": "low"}]}'
    )


async def test_w_doc_uncited_clinical_claim_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A W-DOC synthesis with a numeric clinical claim and zero
    citations must not pass the verifier.

    The fail-closed path replaces the AIMessage with a document-grounded
    refusal so the user never sees an uncited document-derived value
    presented as chart truth.
    """
    from copilot.graph import build_graph

    document_id = "doc-uncited-01"
    uncited_ai = AIMessage(
        content=(
            "The lipid panel shows an LDL of 180 mg/dL which is high. "
            "Consider starting a statin."
        )
    )
    intake_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "extract_document",
                        "args": {"document_id": document_id},
                        "id": "call-extract-uncited",
                    }
                ],
            ),
            ToolMessage(
                content=_document_extract_payload(document_id),
                tool_call_id="call-extract-uncited",
                name="extract_document",
            ),
            uncited_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-DOC", 0.96)],
        intake_script=intake_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.EXTRACT,
                reasoning="Uploaded lab document needs extraction.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="walk me through what's notable")],
            "conversation_id": "conv-uncited-doc",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
            "patient_id": "Patient/p-1",
        },
        _config("conv-uncited-doc"),
    )

    decision = result.get("decision")
    assert decision != "allow", (
        f"verifier must NOT allow an uncited document answer; got "
        f"decision={decision!r}"
    )
    assert decision == "refused_unsourced", (
        f"expected refused_unsourced for uncited document answer; got {decision!r}"
    )

    final = _final_message(result)
    assert isinstance(final, AIMessage)
    text = (final.content or "").lower()
    # Refusal text should reference document grounding so the user knows
    # the gap is provenance, not an evidence corpus issue.
    assert any(
        phrase in text
        for phrase in (
            "document",
            "couldn't ground",
            "no citation",
            "provenance",
            "verify",
        )
    ), f"refusal text should explain document-grounding gap, got: {final.content!r}"


async def test_w_doc_with_document_citation_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: a W-DOC answer that cites the DocumentReference still
    passes the verifier after the new gate is added.
    """
    from copilot.graph import build_graph

    document_id = "doc-cited-01"
    cited_ai = AIMessage(
        content=(
            "The lipid panel shows an LDL of 180 mg/dL "
            f'<cite ref="DocumentReference/{document_id}" page="1" '
            'field="results[0].value" value="180"/>, which the extractor '
            "marked as low-confidence; please verify against the source "
            "before considering treatment recommendations."
        )
    )
    intake_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "extract_document",
                        "args": {"document_id": document_id},
                        "id": "call-extract-cited",
                    }
                ],
            ),
            ToolMessage(
                content=_document_extract_payload(document_id),
                tool_call_id="call-extract-cited",
                name="extract_document",
            ),
            cited_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-DOC", 0.97)],
        intake_script=intake_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.EXTRACT,
                reasoning="Uploaded lab document needs extraction.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="walk me through what's notable")],
            "conversation_id": "conv-cited-doc",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
            "patient_id": "Patient/p-1",
        },
        _config("conv-cited-doc"),
    )

    assert result.get("decision") == "allow"
    final = _final_message(result)
    assert isinstance(final, AIMessage)
    cited = set(_citation_refs(final.content))
    assert f"DocumentReference/{document_id}" in cited


async def test_w_doc_evidence_gap_response_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W-DOC path with a clean evidence-gap admission must not be
    punished by the new gate.

    Mirrors the W-EVD evidence-gap test: an honest "I couldn't ground"
    response is correct behaviour and must reach the user.
    """
    from copilot.graph import build_graph

    gap_ai = AIMessage(
        content=(
            "I couldn't ground a clinical value against the uploaded "
            "document. Please re-attach the file or describe what you "
            "want me to look for."
        )
    )
    intake_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_patient_documents",
                        "args": {"patient_id": "Patient/p-1"},
                        "id": "call-list-empty",
                    }
                ],
            ),
            ToolMessage(
                content='{"ok": true, "documents": []}',
                tool_call_id="call-list-empty",
                name="list_patient_documents",
            ),
            gap_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-DOC", 0.92)],
        intake_script=intake_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.EXTRACT,
                reasoning="No matching document expected.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="what does the lab pdf say")],
            "conversation_id": "conv-gap-doc",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
            "patient_id": "Patient/p-1",
        },
        _config("conv-gap-doc"),
    )

    # Honest "I couldn't ground" admission must reach the user as allow.
    assert result.get("decision") == "allow", (
        f"document-gap honest refusal must pass; got "
        f"decision={result.get('decision')!r}"
    )
