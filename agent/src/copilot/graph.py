"""Co-Pilot StateGraph.

UC-2 per-patient brief: ``agent`` (langchain.agents.create_agent) →
``verifier`` (deterministic citation-resolution check, ARCHITECTURE.md §13) →
END (or back to agent for up to 2 regenerations).

The verifier is the §13 safety contract: every clinical claim must cite a
FHIR resource that was actually fetched in this turn, otherwise the loop
regenerates with feedback. After two failed retries the agent emits an
explicit ``refused_unsourced`` refusal.

Classifier, planner, and UC-1 nodes are tracked in
``agentforge-docs/AGENT-TODO.md`` and will land alongside this spine.
"""

from __future__ import annotations

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
    plain_block_from_text,
    refusal_plain_block,
    synthesize_overnight_block,
    synthesize_triage_block,
)
from .checkpointer import build_memory_checkpointer
from .config import Settings, get_settings
from .llm import build_chat_model
from .prompts import CLARIFY_SYSTEM, CLASSIFIER_SYSTEM, PER_PATIENT_BRIEF, TRIAGE_BRIEF
from .state import CoPilotState
from .tools import make_tools, set_active_patient_id, set_active_smart_token

MAX_REGENS = 2
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.8

# Workflows we have agent wiring for today. Anything outside this set with a
# confident classification still routes through the agent (so the system
# fails open as more workflows light up), but UC-1 triage will get its own
# branch when the two-stage flow lands (AGENT-TODO).
SUPPORTED_WORKFLOWS = {"W-2", "W-7"}
# Panel-spanning workflows: triage_node clears patient context so calls can
# fan out across the care-team panel. Per-patient workflows (W-2..W-9, W-11)
# fall through to agent_node with patient context bound.
TRIAGE_WORKFLOWS = {"W-1", "W-10"}


class WorkflowDecision(BaseModel):
    """Structured output from the classifier node (ARCHITECTURE.md §9 step 3)."""

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


# Tool source labels that disambiguate Observation rows by FHIR category. Used
# to feed the citation-card mapper so cited Observation refs land on the right
# OpenEMR chart card. Aligned with the ``sources_checked`` strings the tools
# in ``tools.py`` already emit.
_OBSERVATION_SOURCE_TO_CATEGORY = {
    "Observation (vital-signs)": "vital-signs",
    "Observation (laboratory)": "laboratory",
}


def _observation_categories_from_tool_message(
    msg: ToolMessage,
) -> dict[str, str]:
    """Extract a {fhir_ref: 'vital-signs' | 'laboratory'} map from a tool result.

    Tools tag their results with ``sources_checked`` already; we read that
    label and apply it to every Observation row in the same payload. This
    runs without parsing JSON — simple substring sniffing is enough because
    we control the producer.
    """

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
    """
    tool_results = state.get("tool_results") or []
    fetched_refs = state.get("fetched_refs") or []
    user_messages = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]

    event = AuditEvent(
        ts=now_iso(),
        conversation_id=state.get("conversation_id") or "",
        user_id=state.get("user_id") or "",
        patient_id=state.get("patient_id") or "",
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
        extra={"final_response_chars": len(final_text) if final_text else 0},
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
        except Exception:  # noqa: BLE001 — classifier failure should fail-open to clarify
            return Command(
                goto="clarify",
                update={"workflow_id": "unclear", "classifier_confidence": 0.0},
            )

        workflow_id = decision.workflow_id
        confidence = decision.confidence

        # Below threshold or explicitly unclear → ask a disambiguating question.
        if workflow_id == "unclear" or confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
            return Command(
                goto="clarify",
                update={"workflow_id": workflow_id, "classifier_confidence": confidence},
            )

        # UC-1 triage gets its own branch (different system prompt + no
        # patient-context binding since triage spans the whole panel).
        if workflow_id in TRIAGE_WORKFLOWS:
            return Command(
                goto="triage",
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
                "I'm not sure whether you want a triage across your panel or a brief "
                "on a specific patient. Could you say which?"
            )
        _audit(state, settings, decision="clarify", final_text=content)
        clarify_block = block_from_clarify_text(content)
        return {
            "messages": [AIMessage(content=content)],
            "decision": "clarify",
            "block": clarify_block.model_dump(by_alias=True),
        }

    async def agent_node(state: CoPilotState) -> dict[str, Any]:
        patient_id = state.get("patient_id") or ""
        feedback = state.get("verifier_feedback") or ""
        smart_token = state.get("smart_access_token") or ""

        # Bind the active SMART patient_id into the tool layer's contextvar so
        # every tool call independently validates per ARCHITECTURE.md §7. The
        # access token rides the same contextvar pattern so FhirClient picks
        # the right authorization for this turn.
        set_active_patient_id(patient_id or None)
        set_active_smart_token(smart_token or None)

        system_prompt = PER_PATIENT_BRIEF.format(patient_id=patient_id)
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
        context_mismatch = False
        observation_categories: dict[str, str] = {}
        for msg in sub_messages:
            if isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if "patient_context_mismatch" in content:
                    context_mismatch = True
                fetched.extend(_refs_from_tool_message(msg))
                observation_categories.update(
                    _observation_categories_from_tool_message(msg)
                )
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        {"name": tc.get("name"), "args": tc.get("args") or {}, "id": tc.get("id")}
                    )

        final = sub_messages[-1] if sub_messages else AIMessage(content="")
        final_text = final.content if isinstance(final.content, str) else str(final.content)

        update: dict[str, Any] = {
            "messages": [final],
            "fetched_refs": fetched,
            "tool_results": tool_calls,
            "observation_categories": observation_categories,
            # Clear feedback so the next verifier pass evaluates the new response cleanly.
            "verifier_feedback": "",
        }
        if context_mismatch:
            # §7: mismatch is a hard deny that takes precedence over the
            # verifier's allow/refused_unsourced decision.
            update["decision"] = "denied_authz"
            update["block"] = plain_block_from_text(final_text).model_dump(by_alias=True)
            return update

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
        # Route the retry back to whichever node produced this turn.
        retry_target = "triage" if state.get("workflow_id") in TRIAGE_WORKFLOWS else "agent"
        return Command(
            goto=retry_target,
            update={"regen_count": regen + 1, "verifier_feedback": feedback},
        )

    async def triage_node(state: CoPilotState) -> dict[str, Any]:
        # Triage explicitly does NOT bind a single active patient; the
        # workflow spans the user's panel. The SMART token still applies —
        # it's the same authenticated user's care team.
        set_active_patient_id(None)
        set_active_smart_token(state.get("smart_access_token") or None)

        feedback = state.get("verifier_feedback") or ""
        system_prompt = TRIAGE_BRIEF
        if feedback:
            system_prompt += f"\n\nVERIFIER FEEDBACK FROM PRIOR ATTEMPT:\n{feedback}\n"

        agent = create_agent(model=chat_model, tools=tools, system_prompt=system_prompt)
        result = await agent.ainvoke({"messages": state.get("messages", [])})

        sub_messages = result.get("messages", [])
        fetched: list[str] = []
        tool_calls: list[dict] = []
        observation_categories: dict[str, str] = {}
        for msg in sub_messages:
            if isinstance(msg, ToolMessage):
                fetched.extend(_refs_from_tool_message(msg))
                observation_categories.update(
                    _observation_categories_from_tool_message(msg)
                )
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        {"name": tc.get("name"), "args": tc.get("args") or {}, "id": tc.get("id")}
                    )

        final = sub_messages[-1] if sub_messages else AIMessage(content="")
        final_text = final.content if isinstance(final.content, str) else str(final.content)

        block = await synthesize_triage_block(
            chat_model,
            synthesis_text=final_text,
            fetched_refs=fetched,
            active_patient_id=state.get("patient_id") or None,
        )
        return {
            "messages": [final],
            "fetched_refs": fetched,
            "tool_results": tool_calls,
            "observation_categories": observation_categories,
            "verifier_feedback": "",
            "block": block.model_dump(by_alias=True),
        }

    builder = StateGraph(CoPilotState)
    builder.add_node("classifier", classifier_node, ends=["agent", "clarify", "triage"])
    builder.add_node("clarify", clarify_node)
    builder.add_node("agent", agent_node)
    builder.add_node("triage", triage_node)
    builder.add_node("verifier", verifier_node, ends=["agent", "triage", END])
    builder.add_edge(START, "classifier")
    builder.add_edge("clarify", END)
    builder.add_edge("agent", "verifier")
    builder.add_edge("triage", "verifier")
    return builder.compile(checkpointer=checkpointer)
