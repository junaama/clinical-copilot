"""Schemas for the Week 2 supervisor + worker dispatch (issue 009).

``SupervisorDecision`` is the structured-output contract between the
supervisor LLM call and the conditional edges that follow it. The
supervisor sees the conversation state and the most recent classifier
hint, then emits one of four actions:

* ``extract`` — dispatch to the intake-extractor worker.
* ``retrieve_evidence`` — dispatch to the evidence-retriever worker.
* ``synthesize`` — workers have returned; ready to write the final answer.
* ``clarify`` — supervisor cannot dispatch yet; ask the user a question.

``HandoffEvent`` is the audit row for every supervisor → worker
transition. It records who decided what at what time, with the reason
the supervisor gave and a non-PHI summary of the worker's input. The
event is frozen so consumers (audit log, Langfuse span) can pass it by
reference without copying.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SupervisorAction(str, Enum):
    """Closed set of supervisor decisions.

    ``str`` mixin lets pydantic accept the bare string from a structured-
    output LLM response (``"extract"``) and resolve it to the enum.
    """

    EXTRACT = "extract"
    RETRIEVE_EVIDENCE = "retrieve_evidence"
    SYNTHESIZE = "synthesize"
    CLARIFY = "clarify"


class SupervisorDecision(BaseModel):
    """One supervisor turn's structured output.

    The LLM is asked for the action plus a one-line reasoning string. The
    reasoning is logged into the ``HandoffEvent`` and surfaced in the
    audit row's ``extra.supervisor_reasoning`` field — it's the reviewer-
    facing explanation for why the worker was picked.
    """

    model_config = ConfigDict(extra="forbid")

    action: SupervisorAction = Field(
        description=(
            "Which action to take next. Must be one of: extract, "
            "retrieve_evidence, synthesize, clarify."
        ),
    )
    reasoning: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "One-line rationale for the chosen action. Will be persisted in "
            "the audit log; do not include raw patient names or document text."
        ),
    )


@dataclass(frozen=True)
class HandoffEvent:
    """Audit row for one supervisor → worker dispatch.

    Fields:

    ``turn_id``
        Composite identifier of the form ``{conversation_id}:turn-{n}``.
        Same shape used elsewhere so consumers can join supervisor events
        against the per-turn ``AuditEvent`` row.
    ``from_node``
        Source node label (``"supervisor"`` for the dispatch case;
        ``"intake_extractor"`` / ``"evidence_retriever"`` when a worker
        hands back).
    ``to_node``
        Destination node label.
    ``reasoning``
        The supervisor's stated reason. Carries forward
        ``SupervisorDecision.reasoning``.
    ``timestamp``
        ISO-8601 UTC string. Use ``copilot.audit.now_iso()`` to generate.
    ``input_summary``
        Non-PHI summary of the worker's input (e.g. ``"patient_id=…,
        document_id=…"`` or ``"query='…', domain_filter=None"``). Patient
        is referenced by id; document text and demographics never appear.
    """

    turn_id: str
    from_node: str
    to_node: str
    reasoning: str
    timestamp: str
    input_summary: str
