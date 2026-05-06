"""Worker nodes for the supervisor sub-graph (issue 009).

Two workers, each bound to a narrow tool surface:

* ``intake_extractor_node`` — owns ``attach_document``,
  ``list_patient_documents``, ``extract_document``, and
  ``get_patient_demographics``. Used when the user uploads / asks about
  a document.
* ``evidence_retriever_node`` — owns ``retrieve_evidence`` and
  ``get_active_problems``. Used when the user asks a guideline question.

The workers themselves are LangChain ``create_agent`` instances. Each
runs to completion (the model decides when to stop calling tools) and
hands back to the supervisor with a HandoffEvent.

Tool wiring uses an allowlist so the worker's tool surface is a subset
of ``make_tools(settings)``. When a listed tool is not yet registered
(because the underlying issue 006 / 008 work hasn't merged), the worker
runs with whatever subset *is* available — no import-time crash, just
narrower capability. This lets issue 009 ship before its data
dependencies and lets the verifier-pass tests run with the W1 tool set.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from ..audit import now_iso
from ..tools import set_active_registry, set_active_smart_token, set_active_user_id
from .schemas import HandoffEvent

_log = logging.getLogger(__name__)


# Per-worker tool allowlists. Names match the StructuredTool .name
# attribute. Tools not yet registered are skipped silently — the worker
# will tell the LLM which tools it has via ``create_agent``.
WORKER_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "intake_extractor": frozenset(
        {
            "attach_document",
            "list_patient_documents",
            "extract_document",
            "get_patient_demographics",
        }
    ),
    "evidence_retriever": frozenset(
        {
            "retrieve_evidence",
            "get_active_problems",
        }
    ),
}


INTAKE_EXTRACTOR_SYSTEM = """\
You are the intake-extractor worker for a clinical Co-Pilot. The
supervisor handed you this turn because the user wants to ingest or
analyze a document. Available tools:

  - list_patient_documents(patient_id, category?)
  - attach_document(patient_id, file_path, doc_type)
  - extract_document(patient_id, document_id, doc_type)
  - get_patient_demographics(patient_id)

Rules:
  - Run only the tool calls you actually need; do not call tools out of
    curiosity.
  - If the user named a document by id, go straight to extract_document.
  - If they referred to "the latest lab", call list_patient_documents
    first and pick the most recent matching the category.
  - Cite the DocumentReference id any time you state a value extracted
    from a document: <cite ref="DocumentReference/{id}" page="{n}"
    field="{path}" value="{literal}"/>.
  - You do not synthesize the final clinical answer — return your
    findings as a short structured note. The supervisor will hand off to
    the synthesizer next.
  - Never restate document text verbatim; cite and quote the literal
    value only.
"""


EVIDENCE_RETRIEVER_SYSTEM = """\
You are the evidence-retriever worker for a clinical Co-Pilot. The
supervisor handed you this turn because the user asked about clinical
guidelines. Available tools:

  - retrieve_evidence(query, top_k=5, domain_filter=None)
  - get_active_problems(patient_id)

Rules:
  - Form a focused search query from the user's question. If the
    question is anchored to a patient (e.g., "what do guidelines say
    about *this* patient's A1C?"), call get_active_problems first to
    sharpen the query with the relevant condition.
  - Call retrieve_evidence once with that query. Read the top-k chunks
    and pass them back to the supervisor as evidence the synthesizer can
    cite.
  - Cite every guideline chunk you reference: <cite
    ref="guideline:{chunk_id}" source="{name}" section="{section}"/>.
  - You do not synthesize the final clinical answer; return the cited
    evidence with a one-line interpretation.
  - If retrieve_evidence returns no relevant chunks, say so explicitly.
    Do not invent guideline text.
"""


def _filter_tools(tools: list[StructuredTool], allowed: frozenset[str]) -> list[StructuredTool]:
    """Return only the tools whose ``.name`` is in ``allowed``.

    Tools missing from ``tools`` are silently skipped — see module
    docstring. Logs at INFO so deploy-time mismatches are discoverable.
    """
    by_name = {t.name: t for t in tools if t.name in allowed}
    missing = sorted(allowed - by_name.keys())
    if missing:
        _log.info(
            "worker tool subset incomplete; missing=%s available=%s",
            missing,
            sorted(by_name.keys()),
        )
    return list(by_name.values())


def build_intake_extractor_node(
    chat_model: BaseChatModel,
    tools: list[StructuredTool],
):
    """Return the intake-extractor worker as an async LangGraph node."""
    worker_tools = _filter_tools(
        tools, WORKER_TOOL_ALLOWLIST["intake_extractor"]
    )
    agent = create_agent(
        model=chat_model,
        tools=worker_tools,
        system_prompt=INTAKE_EXTRACTOR_SYSTEM,
    )

    async def intake_extractor_node(state: dict[str, Any]) -> dict[str, Any]:
        return await _run_worker(
            agent=agent,
            state=state,
            from_node="intake_extractor",
        )

    return intake_extractor_node


def build_evidence_retriever_node(
    chat_model: BaseChatModel,
    tools: list[StructuredTool],
):
    """Return the evidence-retriever worker as an async LangGraph node."""
    worker_tools = _filter_tools(
        tools, WORKER_TOOL_ALLOWLIST["evidence_retriever"]
    )
    agent = create_agent(
        model=chat_model,
        tools=worker_tools,
        system_prompt=EVIDENCE_RETRIEVER_SYSTEM,
    )

    async def evidence_retriever_node(state: dict[str, Any]) -> dict[str, Any]:
        return await _run_worker(
            agent=agent,
            state=state,
            from_node="evidence_retriever",
        )

    return evidence_retriever_node


async def _run_worker(
    *,
    agent,
    state: dict[str, Any],
    from_node: str,
) -> dict[str, Any]:
    """Shared worker driver: run the inner agent, collect refs and tool calls.

    Mirrors the existing ``agent_node`` accumulation pattern in
    ``copilot.graph`` — fetched_refs and tool_results are appended via
    state reducers, gate_decisions is overwritten because it tracks the
    most recent attempt only.
    """
    # Bind tool-layer contextvars from state. Without this the
    # CareTeam gate (user_id) and FHIR/Document clients (smart token)
    # see empty values and short-circuit with "no_active_user" /
    # "no_token". ``agent_node`` does the same at graph.py:471-473.
    set_active_smart_token(state.get("smart_access_token") or None)
    set_active_user_id(state.get("user_id") or None)
    set_active_registry(dict(state.get("resolved_patients") or {}))

    result = await agent.ainvoke({"messages": state.get("messages", [])})
    sub_messages = result.get("messages", [])

    fetched: list[str] = []
    tool_calls: list[dict] = []
    for msg in sub_messages:
        if isinstance(msg, ToolMessage):
            fetched.extend(_extract_refs(msg))
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    {
                        "name": tc.get("name"),
                        "args": tc.get("args") or {},
                        "id": tc.get("id"),
                    }
                )

    # Verifier requires the last message to be an AIMessage. If the
    # sub-agent stopped on a ToolMessage (recursion limit or the LLM
    # didn't emit a final response), fall back to the most-recent
    # AIMessage in the run, or synthesize a minimal one summarizing
    # what the worker fetched. Without this guard the verifier's
    # ``not isinstance(last, AIMessage)`` branch fires and the user
    # sees "I couldn't produce a verifiable response."
    last_ai_message: AIMessage | None = next(
        (m for m in reversed(sub_messages) if isinstance(m, AIMessage) and m.content),
        None,
    )
    if last_ai_message is not None:
        final = last_ai_message
    elif sub_messages:
        ref_summary = ", ".join(_extract_refs_for_summary(sub_messages)) or "no refs"
        final = AIMessage(
            content=(
                f"{from_node} retrieved {len(sub_messages)} sub-message(s) "
                f"but did not emit a synthesis. Refs collected: {ref_summary}."
            )
        )
    else:
        final = AIMessage(content=f"{from_node} produced no output.")

    # Worker → supervisor handoff event so the audit log shows the round
    # trip even when the supervisor would re-dispatch.
    event = HandoffEvent(
        turn_id=_turn_id(state),
        from_node=from_node,
        to_node="supervisor",
        reasoning=f"{from_node} returned {len(tool_calls)} tool call(s).",
        timestamp=now_iso(),
        input_summary=f"fetched_ref_count={len(fetched)}",
    )
    return {
        "messages": [final],
        "fetched_refs": fetched,
        "tool_results": tool_calls,
        "handoff_events": [
            {
                "turn_id": event.turn_id,
                "from_node": event.from_node,
                "to_node": event.to_node,
                "reasoning": event.reasoning,
                "timestamp": event.timestamp,
                "input_summary": event.input_summary,
            }
        ],
    }


def _turn_id(state: dict[str, Any]) -> str:
    conv = state.get("conversation_id") or ""
    user_count = sum(
        1 for m in (state.get("messages") or []) if isinstance(m, HumanMessage)
    )
    return f"{conv}:turn-{user_count}"


# Match the W1 graph's ref pattern. We re-implement the regex inline to
# avoid importing private helpers from copilot.graph (which would
# circle through this module's parent package).
import re  # noqa: E402

_FHIR_REF_PATTERN = re.compile(r'"fhir_ref"\s*:\s*"([^"]+)"')
_DOCUMENT_REF_PATTERN = re.compile(r'"document_ref"\s*:\s*"([^"]+)"')
_GUIDELINE_REF_PATTERN = re.compile(r'"guideline_ref"\s*:\s*"([^"]+)"')


def _extract_refs_for_summary(messages: list) -> list[str]:
    """Best-effort: collect refs across every ToolMessage in ``messages``.

    Used by the no-AIMessage fallback in ``_run_worker`` so the
    placeholder synthesis can name what was retrieved instead of
    returning an opaque empty string.
    """
    refs: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            refs.extend(_extract_refs(msg))
    # Dedup preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def _extract_refs(msg: ToolMessage) -> list[str]:
    """Extract fetched-resource refs from a tool message.

    Three patterns:
    * ``"fhir_ref": "ResourceType/id"`` — existing W1 shape.
    * ``"document_ref": "DocumentReference/id"`` — emitted by the
      issue-006 extraction tools so the verifier can validate document
      citations.
    * ``"guideline_ref": "guideline:chunk_id"`` — emitted by the
      issue-008 retrieve_evidence tool.
    """
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    refs: list[str] = []
    refs.extend(_FHIR_REF_PATTERN.findall(content))
    refs.extend(_DOCUMENT_REF_PATTERN.findall(content))
    refs.extend(_GUIDELINE_REF_PATTERN.findall(content))
    return refs
