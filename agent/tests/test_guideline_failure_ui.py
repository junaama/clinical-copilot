"""Verifier fail-closed for guideline retrieval failures (issue 041).

When the evidence_retriever worker dispatches ``retrieve_evidence`` and
the tool itself fails (``ok: false`` — connection error, no_active_user,
empty_query, etc.), the verifier must produce a corpus-bound limitation
refusal regardless of what the synthesizer wrote. This is a stronger
guarantee than the existing fail-closed for uncited clinical claims:
a *failed* retrieval tool call means the corpus genuinely could not be
consulted this turn, so any answer that does not say so misleads the
clinician about the agent's source contract.

The verifier also scrubs internal-only markers (worker names, raw error
tokens, HTTP statuses) from the final answer when the path is W-EVD.
These technical details belong in traces, not in the clinical answer.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from copilot.supervisor.schemas import SupervisorAction, SupervisorDecision

from .test_graph_integration import (
    _config,
    _FakeAgentScript,
    _final_message,
    _install_graph_stubs,
    _settings,
    _wd,
)


def _retrieve_evidence_failure_payload(error: str) -> str:
    """Build a retrieve_evidence ToolMessage payload with ok:false."""
    return (
        '{"ok": false, "rows": [], "chunks": [], '
        '"sources_checked": ["guideline_corpus"], '
        f'"error": "{error}", "latency_ms": 12}}'
    )


async def test_retrieval_failure_with_clinical_claim_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retrieve_evidence ok:false + LLM fallback to memory → corpus-bound refusal.

    This is the primary safety case: when the corpus cannot be consulted
    and the LLM answers from memory anyway with a clinical claim, the
    verifier must reject with a clean corpus-bound message.
    """
    from copilot.graph import build_graph

    fallback_ai = AIMessage(
        content=(
            "ADA recommends an A1c target below 7.0% for most non-pregnant adults."
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
                        "id": "call-fail-1",
                    }
                ],
            ),
            ToolMessage(
                content=_retrieve_evidence_failure_payload(
                    "retrieval_failed: ConnectionError"
                ),
                tool_call_id="call-fail-1",
                name="retrieve_evidence",
            ),
            fallback_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.93)],
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
            "messages": [HumanMessage(content="What does ADA say about A1c?")],
            "conversation_id": "conv-evd-fail-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-evd-fail-1"),
    )

    assert result.get("decision") == "refused_unsourced", (
        f"failed retrieval must refuse-closed; got {result.get('decision')!r}"
    )

    final = _final_message(result)
    assert isinstance(final, AIMessage)
    text = (final.content or "").lower()
    # Corpus-bound copy.
    assert "guideline" in text or "evidence" in text or "corpus" in text
    # No internal leak markers.
    for marker in (
        "no_active_user",
        "retrieval_failed",
        "connectionerror",
        "evidence_retriever",
        "intake_extractor",
        "http_404",
        "http_500",
    ):
        assert marker not in text, (
            f"refusal must not leak {marker!r}; got: {final.content!r}"
        )


async def test_retrieval_failure_with_internal_leak_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retrieve_evidence ok:false + LLM repeats internal jargon → scrubbed refusal.

    Even when the LLM "honestly" passes through the worker name or the
    raw error token to the user, the verifier must rewrite the answer
    so the clinician never sees those internals.
    """
    from copilot.graph import build_graph

    leaky_ai = AIMessage(
        content=(
            "The evidence_retriever worker hit no_active_user (HTTP 401) when "
            "calling retrieve_evidence — please retry."
        )
    )
    evidence_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_evidence",
                        "args": {"query": "KDIGO ACE"},
                        "id": "call-fail-2",
                    }
                ],
            ),
            ToolMessage(
                content=_retrieve_evidence_failure_payload("no_active_user"),
                tool_call_id="call-fail-2",
                name="retrieve_evidence",
            ),
            leaky_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.92)],
        evidence_script=evidence_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="KDIGO question.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="What do KDIGO guidelines say about ACE inhibitors?")
            ],
            "conversation_id": "conv-evd-fail-2",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-evd-fail-2"),
    )

    assert result.get("decision") == "refused_unsourced"
    final = _final_message(result)
    assert isinstance(final, AIMessage)
    text = (final.content or "").lower()
    # All internals scrubbed.
    for marker in (
        "no_active_user",
        "evidence_retriever",
        "intake_extractor",
        "retrieve_evidence",
        "http 401",
        "retrieval_failed",
    ):
        assert marker not in text, (
            f"refusal must not leak {marker!r}; got: {final.content!r}"
        )
    # Still names the corpus / evidence in user-facing terms.
    assert "guideline" in text or "evidence" in text


async def test_retrieval_empty_with_honest_gap_admission_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ok:true + chunks:[] is the existing evidence-gap path; not a failure.

    Make sure the new failure-closed gate does NOT regress the existing
    behaviour where the corpus was reachable but had no relevant chunks
    and the LLM honestly admitted the gap.
    """
    from copilot.graph import build_graph

    gap_ai = AIMessage(
        content=(
            "I could not find relevant guideline evidence for this question, "
            "so I cannot offer a grounded recommendation."
        )
    )
    evidence_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_evidence",
                        "args": {"query": "obscure"},
                        "id": "call-empty",
                    }
                ],
            ),
            ToolMessage(
                content='{"ok": true, "rows": [], "chunks": [], '
                '"sources_checked": ["guideline_corpus"], '
                '"error": null, "latency_ms": 18}',
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
                reasoning="No matching chunks expected.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="What do guidelines say about <obscure>?")
            ],
            "conversation_id": "conv-evd-gap-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-evd-gap-1"),
    )

    # Empty (but successful) retrieval + honest gap admission still passes.
    assert result.get("decision") == "allow"


async def test_retrieval_failure_route_metadata_is_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guideline retrieval failure surfaces as ``kind: refusal`` (not chart).

    The route metadata helper already maps ``decision in _REFUSAL_DECISIONS``
    to ``kind: refusal`` ahead of the workflow check. This test pins the
    contract: a failed-closed guideline turn must NOT be reported to the
    UI as a chart FHIR read.
    """
    from copilot.api.schemas import derive_route_metadata

    route = derive_route_metadata(
        workflow_id="W-EVD",
        decision="refused_unsourced",
        supervisor_action="retrieve_evidence",
    )
    assert route.kind == "refusal"
    # And not "chart" — that would be the most damaging mislabel.
    assert route.kind != "chart"


async def test_retrieval_success_still_routes_as_guideline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: successful guideline retrieval still routes as ``guideline``."""
    from copilot.api.schemas import derive_route_metadata

    route = derive_route_metadata(
        workflow_id="W-EVD",
        decision="allow",
        supervisor_action="retrieve_evidence",
    )
    assert route.kind == "guideline"
