"""Worker nodes for the supervisor sub-graph (issue 009).

Two workers, each bound to a narrow tool surface:

* ``intake_extractor_node`` — owns ``attach_document``,
  ``list_patient_documents``, ``extract_document``,
  ``get_patient_demographics``, and ``run_per_patient_brief``. Used
  when the user uploads / asks about a document.
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
            "run_per_patient_brief",
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
analyze a document. Your output is clinician decision support over
source evidence — extracted document values are not chart truth and
are not order entry. The clinician remains the decision-maker and
must verify any clinically important value against the source.

Available tools:

  - list_patient_documents(patient_id, category?)
  - attach_document(patient_id, file_path, doc_type)
  - extract_document(patient_id, document_id, doc_type)
  - get_patient_demographics(patient_id)
  - run_per_patient_brief(patient_id, hours=24)

Supported doc_type values: lab_pdf, intake_form, hl7_oru, hl7_adt,
xlsx_workbook, docx_referral, tiff_fax.

Rules:
  - Run only the tool calls you actually need; do not call tools out of
    curiosity.
  - If the user named a document by id, go straight to extract_document.
  - If they referred to "the latest lab", call list_patient_documents
    first and pick the most recent matching the category.
  - For a fresh upload sentinel that includes a Patient/<id>, fetch the
    uploaded document and the patient chart context before writing:
    call extract_document for the document and run_per_patient_brief
    for the patient. Use the chart brief to make "What changed" a real
    comparison against current chart context instead of only saying
    what the upload newly adds. If the brief tool is unavailable or
    returns an error, keep the document answer but say the chart diff
    could not be completed this turn.
  - Cite the DocumentReference id any time you state a value extracted
    from a document: <cite ref="DocumentReference/{id}" page="{n}"
    field="{path}" value="{literal}"/>. Every clinical value drawn from
    the uploaded document must carry a citation in the same sentence.
  - Distinguish chart facts from document facts when you write. Values
    drawn from FHIR resources are chart facts; values drawn from the
    uploaded document are document-sourced facts presented as
    source-linked annotations requiring clinician review. Do not
    promote an extracted value to chart truth and do not present it as
    a chart Observation. Phrase document facts as "the uploaded
    document records ..." or "the lab PDF reports ..." rather than
    asserting the value as if it were a verified chart entry.
  - Surface low-confidence clinically important values as uncertain.
    The extraction schema's ``confidence`` field marks each result as
    "high", "medium", or "low". When a clinically important value
    (lab numeric, vital, dose, allergy, medication name, chief
    concern) is marked "low", state the value and explicitly tag it
    as low-confidence (e.g., "the document records LDL as 180 mg/dL,
    but the extractor marked this value as low-confidence — please
    verify against the source"). Never assert a low-confidence value
    as fact.
  - Low-confidence values must not be the basis for confident
    clinical synthesis. Do not chain a low-confidence extraction into
    a guideline recommendation, a treatment suggestion, or a
    diagnostic conclusion. If the user asks for synthesis grounded on
    a low-confidence value, narrow the answer to "verify the value
    against the source first" and stop.
  - Format document-analysis answers for fast clinician review, not as
    one dense paragraph. Use these exact short Markdown sections with
    blank lines between them:
      ## What changed
      ## Pay attention
      ## Evidence and limits
    In "What changed", say what the upload newly adds. If you did not
    fetch prior chart values or prior documents for comparison, state
    that you can only describe what the upload adds in this turn, not a
    longitudinal chart diff. In "Pay attention", list abnormal,
    safety-relevant, low-confidence, missing, or patient-mismatch
    signals. In "Evidence and limits", separate document evidence from
    guideline evidence. If no guideline chunks were retrieved in this
    turn, say guideline evidence was not retrieved and do not present a
    guideline recommendation.
  - Refuse autonomous-action requests. If the user asks you to place
    an order, prescribe a medication, start/stop/titrate a dose, or
    write to the chart, explain that you provide source-linked
    decision support, not order entry, and offer to surface the
    extracted document values instead.
  - You do not synthesize the final clinical answer — return your
    findings as a short structured note. The supervisor will hand off to
    the synthesizer next.
  - Never restate document text verbatim; cite and quote the literal
    value only.
"""


EVIDENCE_RETRIEVER_SYSTEM = """\
You are the evidence-retriever worker for a clinical Co-Pilot. The
supervisor handed you this turn because the user asked about clinical
guidelines. Your output is clinician decision support, not an
autonomous treatment decision and not order entry — the clinician
remains the decision-maker. Available tools:

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
  - Frame recommendations as decision support: phrase them as
    "guidelines suggest considering X" or "evidence supports X as a
    target". Do not issue directive language ("you should prescribe
    Y", "start the patient on Z", "administer dose D"). The clinician
    chooses; you summarize the evidence.
  - If the user asks for an autonomous action — to place an order, to
    prescribe a medication, to start/stop/titrate a dose, or to enter
    a chart write — refuse cleanly: explain that you provide
    evidence-grounded decision support, not order entry, and offer to
    surface the relevant guideline evidence instead. Never produce a
    directive answer.
  - If retrieve_evidence returns no relevant chunks, say so explicitly
    and stop. Do not invent guideline text. Do not fill the gap from
    your own training data — the corpus is the only authority for
    grounded answers in this turn.
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
    cache_hits: list[str] = []
    for msg in sub_messages:
        if isinstance(msg, ToolMessage):
            fetched.extend(_extract_refs(msg))
            cache_hits.extend(_extract_cache_keys(msg))
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
        # Issue 041: never leak the worker name into user-facing text. The
        # fallback synthesis is a placeholder shown to the clinician when
        # the inner agent stopped without emitting an AIMessage; word it
        # in product language and let the verifier reason about provenance
        # via fetched_refs / citations rather than worker identity.
        ref_summary = ", ".join(_extract_refs_for_summary(sub_messages)) or "no refs"
        final = AIMessage(
            content=(
                f"I gathered source material for this question but did not "
                f"finish the synthesis. Refs collected: {ref_summary}."
            )
        )
    else:
        final = AIMessage(
            content="I could not produce an answer for this question."
        )

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
        "cache_hits": cache_hits,
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
# Issue 030: cache-hit signal emitted by the extraction tool's cache-served
# envelope (see ``tools/extraction.py:_cache_row_envelope``). Used by
# ``_extract_cache_keys`` so the per-turn ``cache_hits`` field on the
# /chat response surfaces a cache-served second extract.
_CACHE_HIT_PATTERN = re.compile(r'"cache_hit"\s*:\s*true', re.IGNORECASE)
_CACHE_KEY_PATTERN = re.compile(r'"cache_key"\s*:\s*"([^"]+)"')

# Issue 026: legacy uploads under the bool-given OpenEMR 500 path used to
# emit synthesized DocumentReference ids of the form
# ``openemr-upload-<sha-hex>``. Issue 022 stopped producing them, but
# checkpointer-stored state from earlier turns may still carry them in
# tool-message payloads. They are not real OpenEMR DocumentReference
# resources, so cited claims against them must not pass verification —
# scrub them before they enter ``fetched_refs``.
_SYNTHETIC_DOC_REF_PREFIX = "DocumentReference/openemr-upload-"


def _is_synthetic_doc_ref(ref: str) -> bool:
    return ref.startswith(_SYNTHETIC_DOC_REF_PREFIX)


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
    refs.extend(
        ref
        for ref in _DOCUMENT_REF_PATTERN.findall(content)
        if not _is_synthetic_doc_ref(ref)
    )
    refs.extend(_GUIDELINE_REF_PATTERN.findall(content))
    return refs


def _extract_cache_keys(msg: ToolMessage) -> list[str]:
    """Return the cache_key for a cache-served ToolMessage, else empty.

    Issue 030: surfaces the per-turn cache-hit signal so the /chat
    response state can carry observable evidence of a cache-served
    extraction. When the message has no ``"cache_hit": true`` marker,
    returns ``[]``. When it does, returns the matching ``"cache_key"``
    value (or a placeholder if the key is missing).
    """
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    if not _CACHE_HIT_PATTERN.search(content):
        return []
    match = _CACHE_KEY_PATTERN.search(content)
    return [match.group(1) if match else "cache_hit"]
