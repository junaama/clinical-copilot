"""Co-Pilot StateGraph.

Issue 003 collapsed the EHR-launch-era ``triage_node`` / ``agent_node`` split
into a single tool-calling node and demoted the classifier to an advisory
hint. The graph topology is now ``classifier → (clarify | agent) → verifier
→ END`` with verifier-driven regen looping back to ``agent``.

The classifier still emits ``{ workflow_id, confidence }`` and the runtime
records both in the audit row, but the values do not gate the tool surface:
all tools are bound to one node and the LLM picks. Workflow-specific
behavior comes from the synthesis prompts (issues 006/007) and from the
LLM choosing composite vs granular tools.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

from .audit import AuditEvent, now_iso, write_audit_event
from .blocks import (
    block_from_clarify_text,
    build_citations,
    extract_cite_attributes,
    plain_block_from_text,
    refusal_plain_block,
    synthesize_overnight_block,
)
from .care_team import AuthDecision
from .checkpointer import build_memory_checkpointer
from .config import Settings, get_settings
from .cost_tracking import (
    CallCost,
    aggregate_turn_cost,
    estimate_call_cost,
)
from .llm import build_chat_model
from .prompts import CLARIFY_SYSTEM, CLASSIFIER_SYSTEM, build_system_prompt
from .state import CoPilotState
from .supervisor.graph import build_supervisor_node, route_after_supervisor
from .supervisor.workers import (
    build_evidence_retriever_node,
    build_intake_extractor_node,
)
from .tools import (
    make_tools,
    set_active_registry,
    set_active_smart_token,
    set_active_user_id,
)

_log = logging.getLogger(__name__)

MAX_REGENS = 2
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.8

# Auth-class errors emitted by the tool layer. Used to map a ToolMessage's
# error payload to a per-call gate decision for the audit row.
_AUTH_DECISIONS: frozenset[str] = frozenset(d.value for d in AuthDecision)
_DENIED_DECISIONS: frozenset[str] = frozenset(
    d.value for d in AuthDecision if d is not AuthDecision.ALLOWED
)


class WorkflowDecision(BaseModel):
    """Structured output from the classifier node (advisory)."""

    workflow_id: str = Field(
        description='One of "W-1"..."W-11" or "unclear"',
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Classifier confidence in [0.0, 1.0]",
    )


_SUPERVISOR_WORKFLOWS: frozenset[str] = frozenset({"W-DOC", "W-EVD"})


def _route_after_classifier(
    *,
    workflow_id: str,
    confidence: float,
    patient_id: str | None,
    focus_pid: str | None,
) -> str:
    """Decide whether the post-classifier edge goes to ``agent``, ``clarify``,
    or the new ``supervisor`` (issue 009 W-DOC / W-EVD routes).

    The classifier sees only the latest user message — it does not know
    whether ``patient_id`` (from session_context, e.g. EHR-launch) or
    ``focus_pid`` (resolved earlier this conversation) is already bound in
    state. For single-patient questions like "What happened to this patient
    overnight?" the classifier reasonably emits ``unclear`` / low confidence,
    which used to route every such turn into ``clarify_node`` (issue 018).

    Routing rules:
    1. Document / evidence intents (W-DOC, W-EVD) ALWAYS go to the
       supervisor regardless of patient binding — the supervisor owns
       the document + retrieval workers.
    2. Whenever a patient is already bound, short-circuit to ``agent``.
    3. Otherwise, if the classifier is unclear or below threshold, fall
       back to ``clarify``.
    4. Otherwise, ``agent``.
    """
    if workflow_id in _SUPERVISOR_WORKFLOWS:
        return "supervisor"
    if (patient_id or "").strip() or (focus_pid or "").strip():
        return "agent"
    if workflow_id == "unclear" or confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
        return "clarify"
    return "agent"


# Match ``<cite ref="X"/>`` and ``<cite ref="X" extra="..."/>``. Issue 009
# extends the citation form with extra attributes (``page``, ``field``,
# ``value``, ``source``, ``section``) for DocumentReference and guideline
# refs; the verifier must capture only the ``ref`` value regardless of
# trailing attributes, otherwise valid document/guideline citations fall
# through as ``unresolved`` and trip the verifier's regen loop.
_CITE_PATTERN = re.compile(
    r'<cite\s+ref\s*=\s*["“”‘’]([^"“”‘’]+)["“”‘’][^>]*/?\s*>',
    flags=re.IGNORECASE,
)
_FHIR_REF_PATTERN = re.compile(r'"fhir_ref"\s*:\s*"([^"]+)"')
_TOOL_ERROR_PATTERN = re.compile(r'"error"\s*:\s*"([^"]+)"')
_TOOL_OK_PATTERN = re.compile(r'"ok"\s*:\s*(true|false)')
_TOOL_STATUS_PATTERN = re.compile(r'"status"\s*:\s*"([^"]+)"')


def _extract_citations(text: str) -> list[str]:
    seen: list[str] = []
    for match in _CITE_PATTERN.finditer(text or ""):
        ref = match.group(1).strip()
        if ref and ref not in seen:
            seen.append(ref)
    return seen


def _refs_from_tool_message(msg: ToolMessage) -> set[str]:
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    return set(_FHIR_REF_PATTERN.findall(content))


# Tool source labels that disambiguate Observation rows by FHIR category.
# Used to feed the citation-card mapper so cited Observation refs land on
# the right OpenEMR chart card.
_OBSERVATION_SOURCE_TO_CATEGORY = {
    "Observation (vital-signs)": "vital-signs",
    "Observation (laboratory)": "laboratory",
}


def _observation_categories_from_tool_message(
    msg: ToolMessage,
) -> dict[str, str]:
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    category: str | None = None
    for source_label, cat in _OBSERVATION_SOURCE_TO_CATEGORY.items():
        if source_label in content:
            category = cat
            break
    if category is None:
        return {}
    return {
        ref: category
        for ref in _FHIR_REF_PATTERN.findall(content)
        if ref.startswith("Observation/")
    }


def _gate_decision_for_tool_message(msg: ToolMessage) -> str:
    """Map a ToolMessage's payload to one ``AuthDecision`` value.

    ``careteam_denied`` / ``patient_context_mismatch`` / ``no_active_patient``
    map to themselves. Anything else (success or non-auth error) collapses
    to ``allowed`` — gate decisions only track authorization, not
    operational outcomes.
    """
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    error_match = _TOOL_ERROR_PATTERN.search(content)
    if error_match is None:
        return AuthDecision.ALLOWED.value
    error = error_match.group(1)
    if error in _AUTH_DECISIONS:
        return error
    return AuthDecision.ALLOWED.value


def _resolved_patients_from_tool_message(
    msg: ToolMessage,
) -> dict[str, dict[str, Any]]:
    """Extract newly-resolved patients from a ``resolve_patient`` ToolMessage.

    Returns a dict keyed on ``patient_id`` carrying the display fields the
    registry stores (given_name, family_name, birth_date). Only ``status:
    "resolved"`` payloads contribute — ambiguous, not_found, and clarify
    intentionally do not populate the registry because the LLM still owes
    the user a follow-up.
    """
    if (msg.name or "") != "resolve_patient":
        return {}
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    status_match = _TOOL_STATUS_PATTERN.search(content)
    if status_match is None or status_match.group(1) != "resolved":
        return {}
    # The payload is JSON; parse it for structured access.
    try:
        import json

        payload = json.loads(content)
    except (ValueError, TypeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in payload.get("patients") or []:
        pid = p.get("patient_id")
        if not pid:
            continue
        out[pid] = {
            "patient_id": pid,
            "given_name": p.get("given_name") or "",
            "family_name": p.get("family_name") or "",
            "birth_date": p.get("birth_date") or "",
        }
    return out


def _per_call_costs(state: CoPilotState, settings: Settings) -> list[CallCost]:
    """One ``CallCost`` per AIMessage with usage metadata, in order.

    LangChain populates ``AIMessage.usage_metadata`` with
    ``input_tokens`` / ``output_tokens`` / ``total_tokens`` (best effort —
    not every provider does). When the metadata is missing the message is
    skipped rather than counted as zero, so a partial answer doesn't
    silently inflate the per-turn rate-known total.

    The model name on each AIMessage is preferred when present (LangChain
    stores it under ``response_metadata.model_name`` / ``model``); we fall
    back to ``settings.llm_model`` so the audit row still names something
    when the provider didn't echo back.
    """
    out: list[CallCost] = []
    for msg in state.get("messages", []) or []:
        if not isinstance(msg, AIMessage):
            continue
        usage = getattr(msg, "usage_metadata", None) or {}
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        if in_tok == 0 and out_tok == 0:
            continue
        rmeta = getattr(msg, "response_metadata", None) or {}
        model = (
            rmeta.get("model_name")
            or rmeta.get("model")
            or settings.llm_model
        )
        out.append(
            estimate_call_cost(
                str(model),
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        )
    return out


def _tool_sequence(state: CoPilotState) -> list[str]:
    """Ordered list of tool names invoked this turn (duplicates kept).

    ``tool_results`` is the canonical record because ``agent_node`` already
    extracts ``{"name", "args", "id"}`` from each AIMessage's tool_calls.
    Falls back to scanning AIMessage.tool_calls directly when the state
    field is empty (e.g., refusal turns where no tool ran but the LLM
    still emitted a malformed tool_call).
    """
    tool_results = state.get("tool_results") or []
    if tool_results:
        return [str(tc.get("name") or "") for tc in tool_results if tc.get("name")]
    seq: list[str] = []
    for msg in state.get("messages", []) or []:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name:
                    seq.append(str(name))
    return seq


def _audit(
    state: CoPilotState,
    settings: Settings,
    *,
    decision: str,
    final_text: str | None = None,
    escalation_reason: str | None = None,
) -> None:
    """Write one ``agent_audit`` row for the turn.

    Free text (user prompt, assistant body) is intentionally NOT recorded
    here — that's the §9 step 11 "encrypted prompts/responses" table's job.
    The audit log carries only structural/decision metadata.

    ``extra.gate_decisions`` and ``extra.denied_count`` carry the per-turn
    gate-decision summary called out in issue 003. ``extra.tool_sequence``,
    ``extra.cost_estimate_usd``, and ``extra.cost_by_model`` carry the
    per-encounter trace data called out in issue 012.
    """
    tool_results = state.get("tool_results") or []
    fetched_refs = state.get("fetched_refs") or []
    user_messages = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
    gate_decisions = list(state.get("gate_decisions") or [])
    denied_count = sum(1 for d in gate_decisions if d in _DENIED_DECISIONS)

    call_costs = _per_call_costs(state, settings)
    turn_cost = aggregate_turn_cost(call_costs)
    prompt_tokens = sum(c.input_tokens for c in call_costs)
    completion_tokens = sum(c.output_tokens for c in call_costs)
    tool_sequence = _tool_sequence(state)

    extra: dict[str, Any] = {
        "final_response_chars": len(final_text) if final_text else 0,
        "gate_decisions": gate_decisions,
        "denied_count": denied_count,
        "tool_sequence": tool_sequence,
        "cost_estimate_usd": turn_cost.total_usd,
    }
    if turn_cost.by_model:
        extra["cost_by_model"] = turn_cost.by_model
    if turn_cost.rate_unknown_models:
        extra["cost_rate_unknown_models"] = turn_cost.rate_unknown_models

    # Issue 009 — when the supervisor sub-graph ran, surface its action
    # and reasoning plus the handoff trail so a reviewer can reconstruct
    # the dispatch path without re-running the pipeline. Absent for W1
    # turns by design.
    supervisor_action = state.get("supervisor_action")
    if supervisor_action:
        extra["supervisor_action"] = supervisor_action
        extra["supervisor_reasoning"] = state.get("supervisor_reasoning") or ""
    handoff_events = state.get("handoff_events") or []
    if handoff_events:
        extra["handoff_events"] = list(handoff_events)

    event = AuditEvent(
        ts=now_iso(),
        conversation_id=state.get("conversation_id") or "",
        user_id=state.get("user_id") or "",
        patient_id=state.get("focus_pid") or state.get("patient_id") or "",
        turn_index=len(user_messages),
        workflow_id=state.get("workflow_id") or "unclear",
        classifier_confidence=float(state.get("classifier_confidence") or 0.0),
        decision=decision,
        regen_count=int(state.get("regen_count") or 0),
        tool_call_count=len(tool_results),
        fetched_ref_count=len(fetched_refs),
        latency_ms=0,  # The graph doesn't track end-to-end latency yet; eval runner does.
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model_provider=settings.llm_provider,
        model_name=settings.llm_model,
        escalation_reason=escalation_reason,
        extra=extra,
    )
    write_audit_event(event, settings)


def build_graph(settings: Settings | None = None, *, checkpointer: Any | None = None):
    """Compile and return the agent graph.

    ``checkpointer`` is injected: callers that need durable persistence open
    an ``AsyncPostgresSaver`` via ``open_checkpointer(settings)`` and pass
    it in. Defaults to an in-process MemorySaver — fine for tests, scripts,
    and demos.
    """
    settings = settings or get_settings()
    chat_model = build_chat_model(settings)
    classifier_model = chat_model.with_structured_output(WorkflowDecision)
    tools = make_tools(settings)
    if checkpointer is None:
        checkpointer = build_memory_checkpointer()

    async def classifier_node(state: CoPilotState) -> Command:
        all_messages = state.get("messages", [])
        user_messages = [m for m in all_messages if isinstance(m, HumanMessage)]
        if not user_messages:
            return Command(
                goto="agent",
                update={"workflow_id": "unclear", "classifier_confidence": 0.0},
            )
        latest = user_messages[-1].content
        latest = latest if isinstance(latest, str) else str(latest)

        # The upload endpoint injects a ``[system] Document uploaded: …``
        # sentinel as a SystemMessage so the classifier prompt can route
        # to W-DOC (prompts.py:55). Surface the most-recent such sentinel
        # alongside the user's text so the classifier sees the context.
        upload_sentinel: str | None = None
        for msg in reversed(all_messages):
            if isinstance(msg, SystemMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.startswith("[system] Document uploaded:"):
                    upload_sentinel = content
                    break

        classifier_input = (
            f"{upload_sentinel}\n\n{latest}" if upload_sentinel else latest
        )

        patient_id = state.get("patient_id")
        focus_pid = state.get("focus_pid")

        try:
            decision = await classifier_model.ainvoke(
                [SystemMessage(content=CLASSIFIER_SYSTEM), HumanMessage(content=classifier_input)]
            )
        except Exception as exc:  # noqa: BLE001 — classifier failure must not crash the turn
            _log.warning(
                "classifier_failed model=%s err=%s: %s",
                settings.llm_model,
                exc.__class__.__name__,
                exc,
                exc_info=True,
            )
            # Fail open to ``agent`` when we already know which patient the
            # user is asking about; otherwise fall back to clarify.
            fallback = _route_after_classifier(
                workflow_id="unclear",
                confidence=0.0,
                patient_id=patient_id,
                focus_pid=focus_pid,
            )
            return Command(
                goto=fallback,
                update={
                    "workflow_id": "unclear",
                    "classifier_confidence": 0.0,
                    # Reset per-turn supervisor state so the hard guard
                    # in supervisor_node doesn't see iterations/refs
                    # from prior turns and force-synthesize without
                    # dispatching a worker (verifier then sees the
                    # user's HumanMessage as last and refuses).
                    "supervisor_iterations": 0,
                },
            )

        workflow_id = decision.workflow_id
        confidence = decision.confidence

        goto = _route_after_classifier(
            workflow_id=workflow_id,
            confidence=confidence,
            patient_id=patient_id,
            focus_pid=focus_pid,
        )
        return Command(
            goto=goto,
            update={
                "workflow_id": workflow_id,
                "classifier_confidence": confidence,
                # Reset per-turn supervisor state — see classifier
                # failure path above for the rationale.
                "supervisor_iterations": 0,
            },
        )

    async def clarify_node(state: CoPilotState) -> dict[str, Any]:
        user_messages = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
        latest = user_messages[-1].content if user_messages else ""
        latest_str = latest if isinstance(latest, str) else str(latest)
        try:
            response = await chat_model.ainvoke(
                [SystemMessage(content=CLARIFY_SYSTEM), HumanMessage(content=latest_str)]
            )
            content = response.content if isinstance(response.content, str) else str(response.content)
        except Exception:  # noqa: BLE001 — never block on clarify failure
            content = (
                "I'm not sure what you want to look at. Could you say which "
                "patient (or which question across your panel)?"
            )
        _audit(state, settings, decision="clarify", final_text=content)
        clarify_block = block_from_clarify_text(content)
        return {
            "messages": [AIMessage(content=content)],
            "decision": "clarify",
            "block": clarify_block.model_dump(by_alias=True),
        }

    async def agent_node(state: CoPilotState) -> dict[str, Any]:
        feedback = state.get("verifier_feedback") or ""
        smart_token = state.get("smart_access_token") or ""
        user_id = state.get("user_id") or ""
        registry = dict(state.get("resolved_patients") or {})
        focus_pid = state.get("focus_pid") or state.get("patient_id") or None

        # Bind context for the tool layer. ``set_active_registry`` lets
        # ``resolve_patient`` do O(1) cache hits on previously-resolved
        # names; the gate consults ``user_id`` directly.
        set_active_smart_token(smart_token or None)
        set_active_user_id(user_id or None)
        set_active_registry(registry)

        system_prompt = build_system_prompt(
            registry=registry,
            focus_pid=focus_pid,
            workflow_id=state.get("workflow_id") or "unclear",
            confidence=float(state.get("classifier_confidence") or 0.0),
        )
        if feedback:
            system_prompt += (
                "\n\nVERIFIER FEEDBACK FROM PRIOR ATTEMPT:\n"
                f"{feedback}\n"
                "Re-issue your response, citing only resources from the fetched set. "
                "If a claim cannot be supported by a fetched resource, drop the claim "
                "or explicitly state the gap."
            )

        agent = create_agent(model=chat_model, tools=tools, system_prompt=system_prompt)

        result = await agent.ainvoke({"messages": state.get("messages", [])})

        sub_messages = result.get("messages", [])
        fetched: list[str] = []
        tool_calls: list[dict] = []
        observation_categories: dict[str, str] = {}
        gate_decisions: list[str] = []
        new_resolved: dict[str, dict[str, Any]] = {}
        for msg in sub_messages:
            if isinstance(msg, ToolMessage):
                fetched.extend(_refs_from_tool_message(msg))
                observation_categories.update(
                    _observation_categories_from_tool_message(msg)
                )
                gate_decisions.append(_gate_decision_for_tool_message(msg))
                new_resolved.update(_resolved_patients_from_tool_message(msg))
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        {"name": tc.get("name"), "args": tc.get("args") or {}, "id": tc.get("id")}
                    )

        final = sub_messages[-1] if sub_messages else AIMessage(content="")
        final_text = final.content if isinstance(final.content, str) else str(final.content)

        # Carry forward the new focus: prefer the most recently resolved pid,
        # falling back to the prior focus when no resolution happened.
        new_focus = focus_pid
        if new_resolved:
            new_focus = next(reversed(new_resolved))

        update: dict[str, Any] = {
            "messages": [final],
            "fetched_refs": fetched,
            "tool_results": tool_calls,
            "observation_categories": observation_categories,
            "gate_decisions": gate_decisions,
            "verifier_feedback": "",
        }
        if new_resolved:
            update["resolved_patients"] = new_resolved
        if new_focus and new_focus != state.get("focus_pid"):
            update["focus_pid"] = new_focus

        # Synthesize the structured overnight block. Validation failures fall
        # back to a PlainBlock inside the helper so the wire shape is always
        # valid even if structured-output parsing breaks.
        block = await synthesize_overnight_block(
            chat_model,
            synthesis_text=final_text,
            fetched_refs=fetched,
            observation_categories=observation_categories,
        )
        update["block"] = block.model_dump(by_alias=True)
        return update

    def verifier_node(state: CoPilotState) -> Command:
        # If the agent_node already set a hard-deny decision (e.g. patient
        # context mismatch), preserve it — verification semantics don't apply.
        existing_decision = state.get("decision")
        if existing_decision in {"denied_authz", "tool_failure", "blocked_baa", "refused_safety"}:
            _audit(state, settings, decision=existing_decision)
            return Command(goto=END)

        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage):
            _audit(state, settings, decision="tool_failure")
            failure_block = refusal_plain_block(
                "I couldn't produce a verifiable response. Please retry."
            )
            return Command(
                goto=END,
                update={
                    "decision": "tool_failure",
                    "block": failure_block.model_dump(by_alias=True),
                },
            )

        text = last.content if isinstance(last.content, str) else str(last.content)
        citations = _extract_citations(text)
        fetched = set(state.get("fetched_refs") or [])
        unresolved = [c for c in citations if c not in fetched]

        if not unresolved:
            _audit(state, settings, decision="allow", final_text=text)
            # Build a fresh PlainBlock from this turn's AIMessage so the
            # UI doesn't render a stale ``block`` left over from a prior
            # turn (e.g., a previous regen-refusal). The supervisor path
            # doesn't synthesize a block itself, so without this update
            # the wire stays pinned to whatever block was set last.
            #
            # Issue 027: ratify the cite tags into Citation objects so
            # guideline / FHIR / document refs survive the trip to the
            # frontend as visible source chips. ``build_citations`` drops
            # any ref not in ``fetched`` — but we already proved
            # ``unresolved`` is empty, so every cited ref makes it.
            ratified_citations = build_citations(
                cited_refs=citations,
                fetched_refs=fetched,
                observation_categories=state.get("observation_categories") or {},
                cite_attributes=extract_cite_attributes(text),
            )
            fresh_block = plain_block_from_text(
                text, citations=ratified_citations
            )
            return Command(
                goto=END,
                update={
                    "decision": "allow",
                    "block": fresh_block.model_dump(by_alias=True),
                },
            )

        regen = state.get("regen_count") or 0
        if regen >= MAX_REGENS:
            refusal_text = (
                "I couldn't ground the following claim(s) against the chart data "
                f"available in this turn: {', '.join(unresolved)}. "
                "These refs do not match any FHIR resource I fetched. "
                "Please rephrase or verify directly in the chart."
            )
            refusal = AIMessage(content=refusal_text)
            refusal_block = refusal_plain_block(refusal_text)
            _audit(
                state,
                settings,
                decision="refused_unsourced",
                escalation_reason=f"unresolved_citations={unresolved}",
            )
            return Command(
                goto=END,
                update={
                    "messages": [refusal],
                    "decision": "refused_unsourced",
                    "block": refusal_block.model_dump(by_alias=True),
                },
            )

        feedback = (
            f"CITATION ERROR: Your prior response cited {unresolved}, which do NOT "
            "exist in any tool result you received this turn. You hallucinated "
            "those references. "
            f"\n\nThe ONLY fetched refs you may cite are: {sorted(fetched)}. "
            "\n\nWhen you redraft:"
            "\n  1. Cite ONLY refs from the fetched list. If a value (BP, lab, dose) "
            "doesn't have a corresponding fetched ref, do NOT state the value — "
            "describe the gap instead (e.g., 'a hypotensive episode is mentioned in "
            "the cross-cover note <cite ref=\"DocumentReference/...\"/>; the "
            "underlying Observation was not retrieved this turn')."
            "\n  2. Do not invent IDs even if a plausible-sounding one fits the "
            "narrative. Plausibility is not existence."
            "\n  3. If you cannot answer the question with the fetched refs, say so "
            "explicitly and stop."
        )
        return Command(
            goto="agent",
            update={"regen_count": regen + 1, "verifier_feedback": feedback},
        )

    # Issue 009: supervisor sub-graph for W-DOC / W-EVD intents. The
    # workers' tool surfaces are subsets of ``tools`` filtered by name,
    # so they're cheap to build at compile time and run with the same
    # CareTeam-gated client wiring as the main agent.
    supervisor_node = build_supervisor_node(chat_model)
    intake_extractor_node = build_intake_extractor_node(chat_model, tools)
    evidence_retriever_node = build_evidence_retriever_node(chat_model, tools)

    builder = StateGraph(CoPilotState)
    builder.add_node(
        "classifier",
        classifier_node,
        ends=["agent", "clarify", "supervisor"],
    )
    builder.add_node("clarify", clarify_node)
    builder.add_node("agent", agent_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("intake_extractor", intake_extractor_node)
    builder.add_node("evidence_retriever", evidence_retriever_node)
    builder.add_node("verifier", verifier_node, ends=["agent", END])
    builder.add_edge(START, "classifier")
    builder.add_edge("clarify", END)
    builder.add_edge("agent", "verifier")
    # After the supervisor decides, dispatch to the worker, the verifier
    # (synthesize), or the clarify node — same conditional pattern as
    # the classifier above.
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "intake_extractor": "intake_extractor",
            "evidence_retriever": "evidence_retriever",
            "verifier": "verifier",
            "clarify": "clarify",
        },
    )
    # Workers loop back to the supervisor so it can synthesize once
    # results are in. The synthesize action then routes to the verifier.
    builder.add_edge("intake_extractor", "supervisor")
    builder.add_edge("evidence_retriever", "supervisor")
    return builder.compile(checkpointer=checkpointer)


# Compatibility re-exports so callers that import the constants for tests
# continue to work — both are advisory now and used only for clarify-route
# decisioning.
__all__ = [
    "CLASSIFIER_CONFIDENCE_THRESHOLD",
    "MAX_REGENS",
    "WorkflowDecision",
    "build_graph",
]
