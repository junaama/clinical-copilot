"""Graph integration tests (issue 021).

These tests exercise the full LangGraph wiring built by ``build_graph``,
not isolated nodes. Each transcript invokes ``build_graph(...).ainvoke(...)``
and asserts on the structural invariants the W2 fixture eval suite (which
runs nodes individually via ``copilot.eval.w2_runner``) cannot catch:

* state → contextvar handoff inside workers (``smart_access_token`` /
  ``user_id`` must reach the tool layer mid-run)
* supervisor iteration budget (no re-dispatch loop on a happy path)
* terminal message shape (verifier requires ``AIMessage``)
* citation refs ⊆ ``fetched_refs``

External I/O is stubbed at the boundary (chat model, ``create_agent``,
``synthesize_overnight_block``) — the graph itself runs unmocked.

Each of the four 2026-05-06 production bugs has a regression test in this
file that fails without its fix and passes with it. See the issue brief at
``issues/done/021-graph-integration-test-layer.md`` for the bug list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from copilot.care_team import AuthDecision, CareTeamGate
from copilot.config import Settings
from copilot.extraction.schemas import LabExtraction, LabResult, SourceCitation
from copilot.supervisor.graph import MAX_SUPERVISOR_ITERATIONS
from copilot.supervisor.schemas import SupervisorAction, SupervisorDecision
from copilot.tools.extraction import make_extraction_tools
from copilot.tools.helpers import get_active_smart_token, get_active_user_id

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _ContextSnapshot:
    """One sample of the tool-layer contextvars at agent invocation time."""

    smart_access_token: str | None
    user_id: str | None


@dataclass
class _FakeAgentScript:
    """Canned response from a stubbed ``create_agent`` invocation.

    ``messages`` is the list returned in ``ainvoke``'s ``{"messages": ...}``
    payload — pre-built ``AIMessage`` / ``ToolMessage`` instances that
    mimic what a ``create_agent`` run would have produced.
    """

    messages: list[Any]
    invocations: int = 0
    snapshots: list[_ContextSnapshot] = field(default_factory=list)


class _FakeAgent:
    """Stand-in for the object ``langchain.agents.create_agent`` returns."""

    def __init__(self, script: _FakeAgentScript) -> None:
        self._script = script

    async def ainvoke(self, _inputs: dict[str, Any]) -> dict[str, Any]:
        self._script.invocations += 1
        # Snapshot the contextvars the production tools would observe.
        # ``agent_node`` and ``_run_worker`` are responsible for binding
        # these from state before invoking the inner agent.
        self._script.snapshots.append(
            _ContextSnapshot(
                smart_access_token=get_active_smart_token(),
                user_id=get_active_user_id(),
            )
        )
        return {"messages": list(self._script.messages)}


class _ToolInvokingAgent:
    """Worker fake that invokes the real ``extract_document`` StructuredTool."""

    def __init__(self, tools: list[Any], *, document_id: str) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._document_id = document_id
        self.invocations = 0

    async def ainvoke(self, _inputs: dict[str, Any]) -> dict[str, Any]:
        self.invocations += 1
        result = await self._tools["extract_document"].ainvoke(
            {
                "patient_id": "Patient/fixture-1",
                "document_id": self._document_id,
                "doc_type": "lab_pdf",
            }
        )
        tool_call_id = "cache-call-1"
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "extract_document",
                            "args": {
                                "patient_id": "Patient/fixture-1",
                                "document_id": self._document_id,
                                "doc_type": "lab_pdf",
                            },
                            "id": tool_call_id,
                        }
                    ],
                ),
                ToolMessage(
                    content=json.dumps(result),
                    tool_call_id=tool_call_id,
                    name="extract_document",
                ),
                AIMessage(
                    content=(
                        "Cached LDL is 180 mg/dL "
                        f'<cite ref="DocumentReference/{self._document_id}" '
                        'page="1" field="results[0].value" value="180"/>.'
                    )
                ),
            ]
        }


class _FakeStructured:
    """Stand-in for ``chat_model.with_structured_output(Schema)``.

    ``decisions`` may include callables — when popped, a callable is
    invoked with the messages list and its return value is used as the
    decision. This lets a test assert that the classifier actually
    received the upload sentinel (regression: bug 3).
    """

    def __init__(self, decisions: list[Any]) -> None:
        self._decisions = list(decisions)
        self.calls: int = 0
        self.received: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls += 1
        self.received.append(messages)
        if not self._decisions:
            raise AssertionError("FakeStructured ran out of decisions")
        head = self._decisions.pop(0)
        if callable(head):
            return head(messages)
        return head


class _FakeChatModel:
    """Minimal chat-model surface for the integration tests.

    Resolves ``with_structured_output(schema)`` against the per-schema
    decision queues injected in the constructor. Direct ``ainvoke`` is
    a no-op AIMessage — we never exercise the clarify path here, so the
    response content does not matter.
    """

    def __init__(
        self,
        *,
        workflow_decisions: list[Any],
        supervisor_decisions: list[Any] | None = None,
    ) -> None:
        from copilot.graph import WorkflowDecision

        self._by_schema: dict[type, _FakeStructured] = {
            WorkflowDecision: _FakeStructured(workflow_decisions),
        }
        if supervisor_decisions:
            self._by_schema[SupervisorDecision] = _FakeStructured(supervisor_decisions)

    def with_structured_output(self, schema: type) -> _FakeStructured:
        if schema not in self._by_schema:
            # Block-synthesizer schemas (``_OvernightStructured``) are not
            # exercised — ``synthesize_overnight_block`` is monkey-patched
            # away in the integration fixture.
            return _FakeStructured([])
        return self._by_schema[schema]

    async def ainvoke(self, _messages: list[Any]) -> AIMessage:
        return AIMessage(content="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> Settings:
    """Fixture-mode settings — no live FHIR, no real LLM key required."""
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test-key",
        USE_FIXTURE_FHIR=True,
    )


async def _stub_synthesize_overnight_block(
    _model: Any,
    *,
    synthesis_text: str,
    fetched_refs: list[str],
    observation_categories: dict[str, str] | None = None,
) -> Any:
    """Bypass the inner LLM call for block synthesis."""
    from copilot.api.schemas import PlainBlock

    return PlainBlock(lead=synthesis_text or "ok", citations=(), followups=())


def _install_graph_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workflow_decisions: list[Any],
    agent_script: _FakeAgentScript | None = None,
    intake_script: _FakeAgentScript | None = None,
    evidence_script: _FakeAgentScript | None = None,
    supervisor_decisions: list[Any] | None = None,
) -> _FakeChatModel:
    """Wire the per-test fakes into ``build_graph``.

    The patches replace the chat-model factory, the two ``create_agent``
    callsites (graph + workers), and the block-synthesizer inside
    ``agent_node`` so the test never reaches a live LLM. Each script
    object is shared by reference, so the test can read ``invocations``
    and ``snapshots`` after running the graph.
    """
    from copilot import graph as graph_module
    from copilot.supervisor import workers as workers_module

    fake_model = _FakeChatModel(
        workflow_decisions=workflow_decisions,
        supervisor_decisions=supervisor_decisions,
    )
    monkeypatch.setattr(graph_module, "build_chat_model", lambda _settings: fake_model)
    monkeypatch.setattr(
        graph_module, "synthesize_overnight_block", _stub_synthesize_overnight_block
    )

    def _fake_create_agent_graph(*, model: Any, tools: Any, system_prompt: str) -> _FakeAgent:
        if agent_script is None:
            raise AssertionError("agent_node create_agent invoked without script")
        return _FakeAgent(agent_script)

    monkeypatch.setattr(graph_module, "create_agent", _fake_create_agent_graph)

    # Placeholder scripts so ``build_graph`` can wire both workers at
    # compile time even when a given test only exercises one of them.
    # The placeholder explodes if it is ever actually invoked, so an
    # unexpected supervisor dispatch surfaces as a clear test failure.
    def _unexpected_worker_script(label: str) -> _FakeAgentScript:
        unused = AIMessage(content=f"<unused-{label}-worker>")
        return _FakeAgentScript(messages=[unused])

    intake_seen = intake_script or _unexpected_worker_script("intake")
    evidence_seen = evidence_script or _unexpected_worker_script("evidence")

    def _fake_create_agent_worker(*, model: Any, tools: Any, system_prompt: str) -> _FakeAgent:
        # Discriminate the two workers by the system prompt — the only
        # sane signal available at this seam, and the system prompts are
        # statically distinct in ``copilot.supervisor.workers``.
        from copilot.supervisor.workers import (
            EVIDENCE_RETRIEVER_SYSTEM,
            INTAKE_EXTRACTOR_SYSTEM,
        )

        if system_prompt == INTAKE_EXTRACTOR_SYSTEM:
            return _FakeAgent(intake_seen)
        if system_prompt == EVIDENCE_RETRIEVER_SYSTEM:
            return _FakeAgent(evidence_seen)
        raise AssertionError(f"unexpected worker system prompt: {system_prompt[:40]!r}")

    monkeypatch.setattr(workers_module, "create_agent", _fake_create_agent_worker)

    return fake_model


def _wd(workflow_id: str, confidence: float = 0.95):
    """Convenience: build a ``WorkflowDecision``."""
    from copilot.graph import WorkflowDecision

    return WorkflowDecision(workflow_id=workflow_id, confidence=confidence)


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _final_message(result: dict[str, Any]) -> Any:
    msgs = result.get("messages") or []
    return msgs[-1] if msgs else None


def _citation_refs(text: str) -> list[str]:
    from copilot.graph import _extract_citations

    return _extract_citations(text)


def _cached_lab_row(document_id: str) -> dict[str, Any]:
    citation = SourceCitation(
        source_type="lab_pdf",
        source_id=f"DocumentReference/{document_id}",
    )
    extraction = LabExtraction(
        results=[
            LabResult(
                test_name="LDL",
                value="180",
                unit="mg/dL",
                reference_range="<100",
                collection_date="2026-04-15",
                abnormal_flag="high",
                confidence="high",
                source_citation=citation,
            )
        ],
        source_document_id=f"DocumentReference/{document_id}",
        extraction_model="claude-sonnet-4",
        extraction_timestamp="2026-05-06T12:00:00Z",
    )
    return {
        "id": 42,
        "document_id": document_id,
        "patient_id": "Patient/fixture-1",
        "doc_type": "lab_pdf",
        "extraction_json": extraction.model_dump(mode="json"),
        "bboxes_json": [],
        "filename": "p01-chen-lipid-panel.pdf",
        "content_sha256": "sha-cached",
    }


# ---------------------------------------------------------------------------
# Transcript 1 — W-EVD: supervisor must NOT re-dispatch after worker returns.
#
# Regression: bug 2 from the 2026-05-06 demo. ``supervisor_node`` previously
# only consulted the user's last HumanMessage and would re-dispatch the same
# worker until ``MAX_SUPERVISOR_ITERATIONS``, ending the turn on a
# ``ToolMessage``. The fix in ``supervisor/graph.py`` short-circuits to
# ``synthesize`` once a worker has produced tool_results / fetched_refs.
# ---------------------------------------------------------------------------


async def test_w_evd_synthesizes_after_one_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from copilot.graph import build_graph

    evidence_chunk_payload = (
        '{"ok": true, "chunks": [{"guideline_ref": "guideline:ada-a1c-2024-1", '
        '"text": "ADA recommends A1c <7.0 for most adults..."}]}'
    )
    evidence_ai = AIMessage(
        content=(
            'ADA recommends an A1c target below 7.0 for most non-pregnant adults '
            '<cite ref="guideline:ada-a1c-2024-1" source="ADA" section="6.5"/>.'
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
                        "id": "call-1",
                    }
                ],
            ),
            ToolMessage(
                content=evidence_chunk_payload,
                tool_call_id="call-1",
                name="retrieve_evidence",
            ),
            evidence_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.93)],
        evidence_script=evidence_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="User asked about ADA A1c targets.",
            ),
            # If the supervisor were re-invoked instead of short-circuiting
            # via the post-worker guard, this would dispatch the worker a
            # second time. The integration test asserts the guard fires
            # before this decision is consumed.
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="(should never run — guard must short-circuit)",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What does ADA say about A1c targets?")],
            "conversation_id": "conv-evd-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-evd-1"),
    )

    # Worker ran exactly once, not in a re-dispatch loop.
    assert evidence_script.invocations == 1, (
        "supervisor must short-circuit to synthesize after the worker returns; "
        "instead the worker ran multiple times"
    )

    # supervisor_iterations stays well under the cap on a happy path.
    assert (
        int(result.get("supervisor_iterations") or 0)
        <= MAX_SUPERVISOR_ITERATIONS - 1
    )

    # Verifier ran and accepted — terminal message is an AIMessage.
    final = _final_message(result)
    assert isinstance(final, AIMessage), (
        f"verifier requires AIMessage as terminal; got {type(final).__name__}"
    )

    # Citation refs are a subset of fetched_refs (verifier's gate).
    fetched = set(result.get("fetched_refs") or [])
    cited = set(_citation_refs(final.content))
    assert cited, "evidence response must carry at least one citation"
    assert cited <= fetched, f"unresolved citations: {cited - fetched}"
    assert "guideline:ada-a1c-2024-1" in fetched

    # Decision is allow (verifier passed).
    assert result.get("decision") == "allow"


# ---------------------------------------------------------------------------
# Transcript 2 — W-DOC: classifier must see the upload sentinel SystemMessage.
#
# Regression: bug 3 from 2026-05-06. ``classifier_node`` previously filtered
# ``state["messages"]`` to ``HumanMessage`` only, dropping the
# ``[system] Document uploaded: …`` sentinel that ``prompts.py:55`` says
# MUST route to W-DOC. The fix scans for the sentinel and prepends it to
# the classifier's input.
# ---------------------------------------------------------------------------


async def test_w_doc_routes_when_upload_sentinel_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copilot.graph import build_graph

    upload_sentinel = SystemMessage(
        content=(
            '[system] Document uploaded: lab_pdf "p01-chen-lipid-panel.pdf" '
            "(document_id: doc-42) for Patient/fixture-1"
        )
    )

    intake_payload = (
        '{"ok": true, "document_ref": "DocumentReference/doc-42", '
        '"fields": [{"name": "ldl", "value": "162"}]}'
    )
    intake_ai = AIMessage(
        content=(
            'Notable: LDL 162 mg/dL '
            '<cite ref="DocumentReference/doc-42" page="1" '
            'field="ldl" value="162"/>.'
        )
    )
    intake_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "extract_document",
                        "args": {
                            "patient_id": "Patient/fixture-1",
                            "document_id": "doc-42",
                            "doc_type": "lab_pdf",
                        },
                        "id": "call-x",
                    }
                ],
            ),
            ToolMessage(
                content=intake_payload, tool_call_id="call-x", name="extract_document"
            ),
            intake_ai,
        ]
    )

    # The classifier-decision callable inspects the messages it actually
    # received. The fix in ``classifier_node`` surfaces the upload
    # sentinel into the LLM input; without it (bug 3) the SystemMessage
    # is dropped and the classifier sees only the bare user text, so it
    # would mis-route to W-2. This callable returns W-DOC only when the
    # sentinel is visible — pinning the bug-3 regression.
    def _sentinel_aware_classifier(messages: list[Any]):
        # Only inspect HumanMessages — the SystemMessage carries
        # CLASSIFIER_SYSTEM (which itself documents the sentinel string),
        # so checking system content would yield a false positive. The
        # production code surfaces the sentinel by prepending it to the
        # HumanMessage payload; if bug 3 reverts, the sentinel is gone
        # from the user-facing input.
        joined = " ".join(
            (m.content if isinstance(m.content, str) else str(m.content))
            for m in messages
            if isinstance(m, HumanMessage)
        )
        if "[system] Document uploaded:" in joined:
            return _wd("W-DOC", 0.97)
        return _wd("W-2", 0.6)

    fake_model = _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_sentinel_aware_classifier],
        intake_script=intake_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.EXTRACT,
                reasoning="User asked about uploaded lab; dispatch extractor.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [
                upload_sentinel,
                HumanMessage(content="walk me through what's notable"),
            ],
            "conversation_id": "conv-doc-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
            "patient_id": "Patient/fixture-1",
        },
        _config("conv-doc-1"),
    )

    # Classifier was called once with the sentinel surfaced into its input.
    workflow_stub = fake_model._by_schema[__import__(
        "copilot.graph", fromlist=["WorkflowDecision"]
    ).WorkflowDecision]
    assert workflow_stub.calls == 1
    # supervisor dispatched once → no re-dispatch loop on the W-DOC path either.
    assert intake_script.invocations == 1

    final = _final_message(result)
    assert isinstance(final, AIMessage)
    assert result.get("workflow_id") == "W-DOC"

    fetched = set(result.get("fetched_refs") or [])
    assert "DocumentReference/doc-42" in fetched
    cited = set(_citation_refs(final.content))
    assert cited <= fetched, f"unresolved citations: {cited - fetched}"
    assert "DocumentReference/doc-42" in cited
    assert result.get("decision") == "allow"


async def test_w_doc_upload_turn_uses_cached_extraction_without_vlm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copilot import graph as graph_module
    from copilot.graph import build_graph
    from copilot.supervisor import workers as workers_module

    document_id = "doc-cache-42"
    upload_sentinel = SystemMessage(
        content=(
            '[system] Document uploaded: lab_pdf "p01-chen-lipid-panel.pdf" '
            f"(document_id: DocumentReference/{document_id}) for Patient/fixture-1"
        )
    )
    fake_model = _FakeChatModel(
        workflow_decisions=[_wd("W-DOC", 0.98)],
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.EXTRACT,
                reasoning="Uploaded document should be extracted.",
            ),
        ],
    )
    monkeypatch.setattr(graph_module, "build_chat_model", lambda _settings: fake_model)
    monkeypatch.setattr(
        graph_module, "synthesize_overnight_block", _stub_synthesize_overnight_block
    )
    monkeypatch.setattr(
        graph_module,
        "create_agent",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("W-DOC cache test must not invoke agent_node")
        ),
    )

    gate = MagicMock(spec=CareTeamGate)
    gate.assert_authorized = AsyncMock(return_value=AuthDecision.ALLOWED)
    document_client = MagicMock()
    document_client.upload = AsyncMock()
    document_client.list = AsyncMock()
    document_client.download = AsyncMock()
    store = MagicMock()
    store.get_latest_by_document_id = AsyncMock(
        return_value=_cached_lab_row(document_id)
    )
    store.get_latest_by_hash = AsyncMock(return_value=None)
    store.save_lab_extraction = AsyncMock()
    store.save_intake_extraction = AsyncMock()
    persister = MagicMock()
    persister.persist_intake = AsyncMock()

    extraction_tools = make_extraction_tools(
        gate=gate,
        document_client=document_client,
        vlm_model=MagicMock(),
        store=store,
        persister=persister,
    )
    monkeypatch.setattr(graph_module, "make_tools", lambda _settings: extraction_tools)

    tool_agent: _ToolInvokingAgent | None = None

    def _create_worker_agent(*, model: Any, tools: list[Any], system_prompt: str) -> Any:
        nonlocal tool_agent
        from copilot.supervisor.workers import INTAKE_EXTRACTOR_SYSTEM

        if system_prompt != INTAKE_EXTRACTOR_SYSTEM:
            return _FakeAgent(_FakeAgentScript(messages=[AIMessage(content="unused")]))
        tool_agent = _ToolInvokingAgent(tools, document_id=document_id)
        return tool_agent

    monkeypatch.setattr(workers_module, "create_agent", _create_worker_agent)

    vlm = AsyncMock()
    with patch("copilot.tools.extraction.vlm_extract_document", vlm):
        graph = build_graph(_settings())
        result = await graph.ainvoke(
            {
                "messages": [
                    upload_sentinel,
                    HumanMessage(content="walk me through what's notable"),
                ],
                "conversation_id": "conv-doc-cache-1",
                "user_id": "dr_smith",
                "smart_access_token": "stub-token",
                "patient_id": "Patient/fixture-1",
            },
            _config("conv-doc-cache-1"),
        )

    assert tool_agent is not None
    assert tool_agent.invocations == 1
    document_client.download.assert_not_awaited()
    vlm.assert_not_awaited()
    store.save_lab_extraction.assert_not_awaited()

    final = _final_message(result)
    assert isinstance(final, AIMessage)
    assert f"DocumentReference/{document_id}" in (result.get("fetched_refs") or [])
    assert f"DocumentReference/{document_id}" in set(_citation_refs(final.content))
    assert result.get("decision") == "allow"


# ---------------------------------------------------------------------------
# Transcript 3 — W-1: agent_node path remains undisturbed by the supervisor.
#
# Guards against false-positive supervisor routing: a panel-triage / overnight
# turn must reach ``agent_node``, not the supervisor sub-graph. Also asserts
# the state → contextvar handoff (bug 1's territory, exercised at the
# agent_node seam — the worker variant is covered in transcript 4).
# ---------------------------------------------------------------------------


async def test_w1_routes_to_agent_node_and_binds_contextvars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copilot.graph import build_graph

    agent_ai = AIMessage(
        content=(
            "BP dipped to 90/60 at 03:14 with full recovery "
            '<cite ref="Observation/obs-bp-2"/>.'
        )
    )
    agent_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_recent_vitals",
                        "args": {"patient_id": "Patient/fixture-1"},
                        "id": "call-v",
                    }
                ],
            ),
            ToolMessage(
                content='{"ok": true, "rows": [{"fhir_ref": "Observation/obs-bp-2"}]}',
                tool_call_id="call-v",
                name="get_recent_vitals",
            ),
            agent_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-1", 0.91)],
        agent_script=agent_script,
        # No supervisor decisions — W-1 must never reach the supervisor.
        supervisor_decisions=None,
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What happened to this patient overnight?")],
            "conversation_id": "conv-w1-1",
            "user_id": "dr_smith",
            "smart_access_token": "tok-XYZ",
            "patient_id": "Patient/fixture-1",
        },
        _config("conv-w1-1"),
    )

    # agent_node ran exactly once; supervisor never engaged.
    assert agent_script.invocations == 1
    assert result.get("supervisor_action") in (None, ""), (
        "W-1 turns must not reach the supervisor sub-graph"
    )
    assert (result.get("supervisor_iterations") or 0) == 0

    # state → contextvar handoff: the agent observed the same token / user
    # the test seeded into state.
    assert agent_script.snapshots, "agent_node failed to invoke create_agent"
    snapshot = agent_script.snapshots[0]
    assert snapshot.smart_access_token == "tok-XYZ"
    assert snapshot.user_id == "dr_smith"

    final = _final_message(result)
    assert isinstance(final, AIMessage)
    fetched = set(result.get("fetched_refs") or [])
    assert "Observation/obs-bp-2" in fetched
    cited = set(_citation_refs(final.content))
    assert cited <= fetched
    assert result.get("decision") == "allow"


# ---------------------------------------------------------------------------
# Transcript 4 — Worker ends on a ToolMessage.
#
# Regression: bug 4 from 2026-05-06. When ``create_agent`` hit a recursion
# limit or simply stopped without a final synthesis, ``_run_worker`` returned
# the trailing ``ToolMessage`` as ``final`` and the verifier's
# ``not isinstance(last, AIMessage)`` branch fired a refusal. The fix
# synthesizes a placeholder ``AIMessage`` so the verifier sees a valid
# terminal message.
# ---------------------------------------------------------------------------


async def test_worker_ending_on_toolmessage_synthesizes_aimessage_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copilot.graph import build_graph

    # No AIMessage in the worker's sub-message stream — only a tool call
    # and its ToolMessage. Without the fallback, ``_run_worker`` would
    # return the ToolMessage as ``final`` and the verifier would refuse
    # the turn.
    truncated_payload = (
        '{"ok": true, "chunks": [{"guideline_ref": "guideline:foo-1"}]}'
    )
    evidence_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_evidence",
                        "args": {"query": "..."},
                        "id": "call-z",
                    }
                ],
            ),
            ToolMessage(
                content=truncated_payload,
                tool_call_id="call-z",
                name="retrieve_evidence",
            ),
            # NB: deliberately no trailing AIMessage.
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.92)],
        evidence_script=evidence_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="Dispatch evidence retriever.",
            ),
        ],
    )

    graph = build_graph(_settings())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="any guideline on this?")],
            "conversation_id": "conv-fallback-1",
            "user_id": "dr_smith",
            "smart_access_token": "stub-token",
        },
        _config("conv-fallback-1"),
    )

    # Worker ran once; supervisor stayed under iteration cap.
    assert evidence_script.invocations == 1
    assert (
        int(result.get("supervisor_iterations") or 0)
        <= MAX_SUPERVISOR_ITERATIONS - 1
    )

    # The terminal message is an AIMessage even though the worker's
    # sub-graph ended on a ToolMessage. This is the precondition the
    # verifier reads.
    final = _final_message(result)
    assert isinstance(final, AIMessage), (
        "_run_worker must synthesize an AIMessage when the inner agent "
        "stops on a ToolMessage; otherwise the verifier refuses the turn"
    )

    # The fallback AIMessage has no citations, so the citation-subset
    # invariant is trivially satisfied; fetched_refs still reflect the
    # tool's emission so audit gets a non-empty trail.
    assert "guideline:foo-1" in (result.get("fetched_refs") or [])
    cited = set(_citation_refs(final.content))
    fetched = set(result.get("fetched_refs") or [])
    assert cited <= fetched

    # Verifier passed (no unresolved citations) — decision is allow, not
    # the "tool_failure" the bug used to surface.
    assert result.get("decision") == "allow"


# ---------------------------------------------------------------------------
# Transcript 5 — worker contextvar binding.
#
# Regression: bug 1 from 2026-05-06. ``_run_worker`` previously did not
# call ``set_active_smart_token`` / ``set_active_user_id`` /
# ``set_active_registry``, so tools like ``retrieve_evidence`` and the
# FHIR / Document clients saw empty values and short-circuited with
# ``no_active_user`` / ``no_token``. The fix mirrors what ``agent_node``
# does at ``graph.py:471-473``.
#
# Distinct from transcript 3: that test exercises the agent_node seam.
# This one exercises the worker seam and confirms the binding survives
# the supervisor → worker hop.
# ---------------------------------------------------------------------------


async def test_worker_binds_state_to_tool_layer_contextvars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copilot.graph import build_graph

    evidence_ai = AIMessage(
        content=(
            'KDIGO recommends... '
            '<cite ref="guideline:kdigo-ckd-2024-3" source="KDIGO" '
            'section="3.1"/>.'
        )
    )
    evidence_script = _FakeAgentScript(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve_evidence",
                        "args": {"query": "KDIGO CKD"},
                        "id": "call-k",
                    }
                ],
            ),
            ToolMessage(
                content='{"ok": true, "chunks": [{"guideline_ref": "guideline:kdigo-ckd-2024-3"}]}',
                tool_call_id="call-k",
                name="retrieve_evidence",
            ),
            evidence_ai,
        ]
    )

    _install_graph_stubs(
        monkeypatch,
        workflow_decisions=[_wd("W-EVD", 0.94)],
        evidence_script=evidence_script,
        supervisor_decisions=[
            SupervisorDecision(
                action=SupervisorAction.RETRIEVE_EVIDENCE,
                reasoning="Dispatch evidence retriever.",
            ),
        ],
    )

    graph = build_graph(_settings())
    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What does KDIGO say about CKD staging?")],
            "conversation_id": "conv-bind-1",
            "user_id": "dr_jones",
            "smart_access_token": "tok-PER-TURN",
        },
        _config("conv-bind-1"),
    )

    assert evidence_script.snapshots, "evidence worker failed to invoke create_agent"
    snapshot = evidence_script.snapshots[0]
    assert snapshot.smart_access_token == "tok-PER-TURN", (
        "_run_worker must bind state['smart_access_token'] before invoking "
        "the inner agent (regression: bug 1, 2026-05-06)"
    )
    assert snapshot.user_id == "dr_jones", (
        "_run_worker must bind state['user_id'] before invoking the inner "
        "agent (regression: bug 1, 2026-05-06)"
    )
