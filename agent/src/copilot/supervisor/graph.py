"""Supervisor node + post-supervisor routing (issue 009).

The supervisor node runs once per turn after the classifier has decided
the user wants a document workflow (``W-DOC``) or evidence retrieval
(``W-EVD``). It calls the LLM with a structured-output schema
(``SupervisorDecision``) to pick exactly one of:

* ``extract`` → dispatch to ``intake_extractor`` worker
* ``retrieve_evidence`` → dispatch to ``evidence_retriever`` worker
* ``synthesize`` → workers have returned, hand the conversation to the
  verifier
* ``clarify`` → the supervisor cannot dispatch yet; ask the user a
  question

Every dispatch produces a ``HandoffEvent`` that callers persist via the
audit log + Langfuse spans.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ..audit import now_iso
from .schemas import HandoffEvent, SupervisorAction, SupervisorDecision

_log = logging.getLogger(__name__)

# Hard cap on supervisor LLM calls per turn. Each worker dispatch costs
# one supervisor call to dispatch + one to re-evaluate after the worker
# returns, so 4 covers two worker round-trips plus the final synthesize
# decision with one extra call as a buffer. Beyond that we suspect the
# LLM is stuck and force ``synthesize`` to break the loop.
MAX_SUPERVISOR_ITERATIONS = 4


SUPERVISOR_SYSTEM = """\
You are the supervisor of a clinical Co-Pilot's document and evidence
sub-graph. The classifier has decided this turn is either about a
document the clinician uploaded (W-DOC) or about clinical guidelines
(W-EVD). Your job is to pick exactly one action.

Action options:
  - extract           — a document needs to be ingested or analyzed.
                        Dispatches to the intake_extractor worker, which
                        owns: attach_document, list_patient_documents,
                        extract_document, get_patient_demographics.
  - retrieve_evidence — the clinician asked about clinical guidelines.
                        Dispatches to the evidence_retriever worker,
                        which owns: retrieve_evidence,
                        get_active_problems.
  - synthesize        — workers have already produced their results;
                        hand the turn to the synthesis / verifier path.
                        Pick this once and only once after all required
                        workers have run.
  - clarify           — you cannot dispatch yet. Pick this when:
                        * The user asked about a document but no
                          document_id is on the turn and no document is
                          named.
                        * The user asked about guidelines but no concrete
                          medical question is articulated.

Rules:
  - Output JSON only. The runtime parses your output as a structured
    SupervisorDecision: {action, reasoning}.
  - reasoning is one short sentence (no PHI, no document text, no patient
    names — patients are referenced by id).
  - Pick synthesize only when the conversation already shows worker
    output for the question being asked. If you're unsure whether
    workers have run, dispatch the worker again — duplicate work is
    cheaper than missing work.
  - Pick clarify rarely. The classifier already screens out off-topic
    turns, so most turns reaching you can be dispatched.
"""


def build_supervisor_node(chat_model: BaseChatModel):
    """Return an async callable that runs one supervisor turn.

    Lazily binds ``with_structured_output(SupervisorDecision)`` so the
    binding cost only occurs at graph compile, not on every turn.
    """
    structured = chat_model.with_structured_output(SupervisorDecision)

    async def supervisor_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", []) or []
        # Take the last user message as the supervisor's primary input.
        user_messages = [m for m in messages if isinstance(m, HumanMessage)]
        latest = user_messages[-1].content if user_messages else ""
        latest_str = latest if isinstance(latest, str) else str(latest)

        # The classifier's hint is informative for the supervisor — it
        # already decided W-DOC vs W-EVD. Forward it inline so the
        # supervisor doesn't re-classify.
        workflow_id = state.get("workflow_id") or "unclear"
        prefix = (
            f"[classifier hint: workflow_id={workflow_id}]\n\n"
            "User turn:\n"
        )

        iterations = int(state.get("supervisor_iterations") or 0)
        # Hard guard against re-dispatch loops: once a worker has produced
        # tool results or fetched refs, the supervisor's job is done —
        # synthesize. The LLM-based decision sees only the original user
        # message and would re-dispatch the same worker indefinitely
        # otherwise (Langfuse showed cap-hit traces).
        prior_tool_results = state.get("tool_results") or []
        prior_fetched_refs = state.get("fetched_refs") or []
        worker_already_ran = bool(prior_tool_results) or bool(prior_fetched_refs)

        if iterations >= MAX_SUPERVISOR_ITERATIONS:
            decision = SupervisorDecision(
                action=SupervisorAction.SYNTHESIZE,
                reasoning=(
                    "Supervisor iteration cap reached; forcing synthesis to "
                    "avoid runaway dispatch."
                ),
            )
        elif iterations >= 1 and worker_already_ran:
            decision = SupervisorDecision(
                action=SupervisorAction.SYNTHESIZE,
                reasoning=(
                    f"Worker dispatched and produced "
                    f"{len(prior_tool_results)} tool result(s) / "
                    f"{len(prior_fetched_refs)} ref(s); synthesizing."
                ),
            )
        else:
            try:
                decision = await structured.ainvoke(
                    [
                        SystemMessage(content=SUPERVISOR_SYSTEM),
                        HumanMessage(content=prefix + latest_str),
                    ]
                )
            except Exception as exc:
                _log.warning(
                    "supervisor_failed err=%s: %s",
                    exc.__class__.__name__,
                    exc,
                    exc_info=True,
                )
                decision = SupervisorDecision(
                    action=SupervisorAction.CLARIFY,
                    reasoning="Supervisor LLM call failed; ask the user to retry.",
                )

        # Build a HandoffEvent for the dispatch. The audit consumer
        # writes it; we just attach to state.
        turn_id = _format_turn_id(state)
        target = _action_to_node(decision.action)
        event = HandoffEvent(
            turn_id=turn_id,
            from_node="supervisor",
            to_node=target,
            reasoning=decision.reasoning,
            timestamp=now_iso(),
            input_summary=_summarize_input(state),
        )

        return {
            "supervisor_action": decision.action.value,
            "supervisor_reasoning": decision.reasoning,
            "supervisor_iterations": iterations + 1,
            "handoff_events": [_event_as_dict(event)],
        }

    return supervisor_node


def route_after_supervisor(state: dict[str, Any]) -> str:
    """Conditional-edge function for the post-supervisor dispatch."""
    action = state.get("supervisor_action") or SupervisorAction.CLARIFY.value
    return _action_to_node(SupervisorAction(action))


def _action_to_node(action: SupervisorAction) -> str:
    """Map a supervisor action to a graph node label."""
    if action is SupervisorAction.EXTRACT:
        return "intake_extractor"
    if action is SupervisorAction.RETRIEVE_EVIDENCE:
        return "evidence_retriever"
    if action is SupervisorAction.SYNTHESIZE:
        return "verifier"
    return "clarify"


def _format_turn_id(state: dict[str, Any]) -> str:
    conv = state.get("conversation_id") or ""
    # Approximate turn index from the message history so the audit row
    # joins to the per-turn AuditEvent without needing the runtime to
    # thread a separate counter.
    user_count = sum(
        1
        for m in (state.get("messages") or [])
        if isinstance(m, HumanMessage)
    )
    return f"{conv}:turn-{user_count}"


def _summarize_input(state: dict[str, Any]) -> str:
    """Build a non-PHI ``input_summary`` for the handoff event.

    Includes structured ids the workers will use, never patient names or
    document text.
    """
    parts: list[str] = []
    pid = state.get("focus_pid") or state.get("patient_id")
    if pid:
        parts.append(f"patient_id={pid}")
    workflow = state.get("workflow_id")
    if workflow:
        parts.append(f"workflow_id={workflow}")
    if not parts:
        return "(no bound context)"
    return ", ".join(parts)


def _event_as_dict(event: HandoffEvent) -> dict[str, Any]:
    """Return a plain dict so LangGraph can serialize it through the
    checkpointer. ``HandoffEvent`` is frozen so we cannot just hand it
    off — the Postgres saver pickles JSON-compatible payloads only.
    """
    return {
        "turn_id": event.turn_id,
        "from_node": event.from_node,
        "to_node": event.to_node,
        "reasoning": event.reasoning,
        "timestamp": event.timestamp,
        "input_summary": event.input_summary,
    }
