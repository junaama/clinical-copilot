"""Shared state for the Co-Pilot graph.

ARCHITECTURE.md §7 binds a conversation to one patient context. The state
carries that binding plus the message history and the verifier loop's
bookkeeping (fetched refs, regen count, feedback, decision label).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class CoPilotState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]

    conversation_id: str
    patient_id: str
    user_id: str
    smart_access_token: str

    workflow_id: str
    classifier_confidence: float

    tool_results: Annotated[list[dict], operator.add]

    # Verifier loop bookkeeping (ARCHITECTURE.md §13).
    fetched_refs: Annotated[list[str], operator.add]
    regen_count: int
    verifier_feedback: str
    decision: str

    # Categories for fetched Observation refs, populated by the agent/triage
    # nodes during synthesis. Used to disambiguate ``vitals`` vs ``labs``
    # citation cards (CHAT-API-CONTRACT.md).
    observation_categories: dict[str, str]

    # Structured wire block emitted by the synthesis node, carried through
    # to server.py so it can be returned in ChatResponse.
    block: dict[str, Any]
