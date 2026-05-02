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
    refusal_plain_block,
    synthesize_overnight_block,
)
from .care_team import AuthDecision
from .checkpointer import build_memory_checkpointer
from .config import Settings, get_settings
from .llm import build_chat_model
from .prompts import CLARIFY_SYSTEM, CLASSIFIER_SYSTEM, build_system_prompt
from .state import CoPilotState
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


_CITE_PATTERN = re.compile(
    r'<cite\s+ref\s*=\s*["“”‘’]([^"“”‘’]+)["“”‘’]\s*/?\s*>',
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
    gate-decision summary called out in issue 003.
    """
    tool_results = state.get("tool_results") or []
    fetched_refs = state.get("fetched_refs") or []
    user_messages = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
    gate_decisions = list(state.get("gate_decisions") or [])
    denied_count = sum(1 for d in gate_decisions if d in _DENIED_DECISIONS)

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
        prompt_tokens=0,
        completion_tokens=0,
        model_provider=settings.llm_provider,
        model_name=settings.llm_model,
        escalation_reason=escalation_reason,
        extra={
            "final_response_chars": len(final_text) if final_text else 0,
            "gate_decisions": gate_decisions,
            "denied_count": denied_count,
        },
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
        user_messages = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
        if not user_messages:
            return Command(
                goto="agent",
                update={"workflow_id": "unclear", "classifier_confidence": 0.0},
            )
        latest = user_messages[-1].content
        latest = latest if isinstance(latest, str) else str(latest)

        try:
            decision = await classifier_model.ainvoke(
                [SystemMessage(content=CLASSIFIER_SYSTEM), HumanMessage(content=latest)]
            )
        except Exception as exc:  # noqa: BLE001 — classifier failure fails open to clarify
            _log.warning(
                "classifier_failed model=%s err=%s: %s",
                settings.llm_model,
                exc.__class__.__name__,
                exc,
                exc_info=True,
            )
            return Command(
                goto="clarify",
                update={"workflow_id": "unclear", "classifier_confidence": 0.0},
            )

        workflow_id = decision.workflow_id
        confidence = decision.confidence

        # Below threshold or explicitly unclear → ask a disambiguating
        # question. The classifier is otherwise advisory and never gates
        # the tool surface — the only routing decision left is
        # clarify-vs-agent.
        if workflow_id == "unclear" or confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
            return Command(
                goto="clarify",
                update={"workflow_id": workflow_id, "classifier_confidence": confidence},
            )

        return Command(
            goto="agent",
            update={"workflow_id": workflow_id, "classifier_confidence": confidence},
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
            return Command(goto=END, update={"decision": "allow"})

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

    builder = StateGraph(CoPilotState)
    builder.add_node("classifier", classifier_node, ends=["agent", "clarify"])
    builder.add_node("clarify", clarify_node)
    builder.add_node("agent", agent_node)
    builder.add_node("verifier", verifier_node, ends=["agent", END])
    builder.add_edge(START, "classifier")
    builder.add_edge("clarify", END)
    builder.add_edge("agent", "verifier")
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
