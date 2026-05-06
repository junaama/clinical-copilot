"""Shared state for the Co-Pilot graph.

Issue 003 removes the EHR-launch-era ``one patient per conversation`` pin and
introduces a conversation-scoped patient registry that grows monotonically as
the user mentions patients. ``focus_pid`` points at whichever patient was
resolved most recently — that's the implicit subject of follow-ups like
"and his labs?".

State carries the message history, the verifier loop's bookkeeping, and the
per-turn gate decisions that the audit row summarizes.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def _merge_resolved(
    left: dict[str, dict[str, Any]] | None,
    right: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Reducer for ``resolved_patients`` — right wins on key collision.

    The registry grows monotonically within a conversation; right-wins lets a
    later turn refresh a patient's display fields without losing earlier
    entries. ``operator.or_`` would also work for ``dict | dict`` but a named
    reducer makes the merge semantics explicit at the field declaration.
    """
    return {**(left or {}), **(right or {})}


class CoPilotState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]

    conversation_id: str
    patient_id: str
    user_id: str
    smart_access_token: str

    # Classifier output is now advisory: workflow_id and confidence are
    # logged in the audit row and rendered into the system prompt as a hint,
    # but do not gate the tool surface.
    workflow_id: str
    classifier_confidence: float

    tool_results: Annotated[list[dict], operator.add]

    # Conversation-scoped patient registry. Maps ``patient_id`` -> a dict
    # carrying display fields (``given_name``, ``family_name``,
    # ``birth_date``). Populated by the ``resolve_patient`` tool through the
    # agent_node's tool-message scan. Grows monotonically; the reducer is
    # right-wins so a later turn can refresh a stale entry without erasing
    # earlier ones.
    resolved_patients: Annotated[dict[str, dict[str, Any]], _merge_resolved]
    focus_pid: str

    # Verifier loop bookkeeping (ARCHITECTURE.md §13).
    fetched_refs: Annotated[list[str], operator.add]
    regen_count: int
    verifier_feedback: str
    decision: str

    # Per-tool-call gate decisions for the most recent agent_node attempt.
    # Each entry is one of the ``AuthDecision`` values (typically "allowed").
    # On verifier-driven regen, the field is overwritten with the new
    # attempt's decisions — the audit row reflects whatever the final
    # attempt did.
    gate_decisions: list[str]

    # Categories for fetched Observation refs, populated by the agent node
    # during synthesis. Used to disambiguate ``vitals`` vs ``labs`` citation
    # cards (CHAT-API-CONTRACT.md).
    observation_categories: dict[str, str]

    # Structured wire block emitted by the synthesis node, carried through
    # to server.py so it can be returned in ChatResponse.
    block: dict[str, Any]

    # Issue 009 — supervisor sub-graph fields. The supervisor records its
    # last action (extract / retrieve_evidence / synthesize / clarify)
    # and reasoning on every turn so the audit row + Langfuse spans can
    # tell the dispatch story. ``handoff_events`` accumulates per-turn
    # supervisor↔worker transitions; the reducer is plain append (no
    # right-wins semantics — every event is significant).
    # ``supervisor_iterations`` is a per-turn safety counter — the
    # supervisor coerces to ``synthesize`` once it exceeds
    # ``MAX_SUPERVISOR_ITERATIONS`` (set in graph.py) so a misbehaving
    # LLM cannot infinite-loop between supervisor and workers.
    supervisor_action: str
    supervisor_reasoning: str
    supervisor_iterations: int
    handoff_events: Annotated[list[dict[str, Any]], operator.add]
