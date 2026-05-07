"""Pydantic models that mirror ``agentforge-docs/CHAT-API-CONTRACT.md``.

Frozen, strictly-typed DTOs. The discriminated union on ``Block`` lets FastAPI
validate inbound and outbound shapes without per-handler branching, and gives
the frontend a single source of truth for codegen.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Closed set of citation-card values. Anything else collapses to "other" so the
# frontend's postMessage dispatch never receives an unknown card name.
CitationCard = Literal[
    "vitals",
    "labs",
    "medications",
    "problems",
    "allergies",
    "prescriptions",
    "encounters",
    "documents",
    "guideline",
    "other",
]

TrendDirection = Literal["up", "down", "flat"]
DeltaDirection = Literal["up", "down", "flat"]
TimelineKind = Literal[
    "Lab", "Order", "Med admin", "Nursing note", "Imaging", "Vital", "Other"
]


class _Frozen(BaseModel):
    """Common base for wire DTOs — frozen, extra forbidden, alias-aware."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ChatRequest(_Frozen):
    """Inbound POST /chat body.

    ``patient_id``, ``user_id``, and ``smart_access_token`` are optional in
    the body — the server falls back to the SMART token bundle keyed by
    ``conversation_id`` and rejects with 401 if neither is available. This
    keeps fixture/dev runs ergonomic without weakening prod authn.
    """

    conversation_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    patient_id: str = Field(default="")
    user_id: str = Field(default="")
    smart_access_token: str = Field(default="")


class Citation(_Frozen):
    """One verifier-ratified citation pointing at a chart card."""

    card: CitationCard
    label: str
    fhir_ref: str | None = None


class CohortPatient(_Frozen):
    """One row in a UC-1 triage cohort.

    ``self`` is a wire field but a Python identifier-trap — we expose it
    internally as ``is_self`` and alias it on the wire so JSON consumers see
    the contract-correct name.
    """

    id: str
    name: str
    age: int
    room: str
    score: int = Field(ge=0, le=100)
    trend: TrendDirection
    reasons: tuple[str, ...]
    is_self: bool = Field(default=False, alias="self")
    fhir_ref: str | None = None


class Delta(_Frozen):
    """One UC-2 vital/lab delta."""

    label: str
    from_: str = Field(alias="from")
    to: str
    dir: DeltaDirection


class TimelineEvent(_Frozen):
    """One UC-2 timeline event."""

    t: str
    kind: TimelineKind
    text: str
    fhir_ref: str | None = None


class TriageBlock(_Frozen):
    """UC-1 cohort ranking block."""

    kind: Literal["triage"] = "triage"
    lead: str
    cohort: tuple[CohortPatient, ...]
    citations: tuple[Citation, ...] = ()
    followups: tuple[str, ...] = ()


class OvernightBlock(_Frozen):
    """UC-2 per-patient overnight summary block."""

    kind: Literal["overnight"] = "overnight"
    lead: str
    deltas: tuple[Delta, ...]
    timeline: tuple[TimelineEvent, ...]
    citations: tuple[Citation, ...] = ()
    followups: tuple[str, ...] = ()


class PlainBlock(_Frozen):
    """Fallback: clarify, refusal, or out-of-scope free-text answer."""

    kind: Literal["plain"] = "plain"
    lead: str
    citations: tuple[Citation, ...] = ()
    followups: tuple[str, ...] = ()


Block = Annotated[
    TriageBlock | OvernightBlock | PlainBlock,
    Field(discriminator="kind"),
]


# Closed set of route-metadata kinds. Frontend renders a user-facing label
# from this value; the kind is the stable wire identifier the UI dispatches
# on for header copy and badge styling.
RouteKind = Literal[
    "chart",
    "panel",
    "guideline",
    "document",
    "clarify",
    "refusal",
]


class RouteMetadata(_Frozen):
    """Structured route transparency carried alongside every chat answer.

    ``kind`` is the closed-set wire identifier (frontend dispatches on it).
    ``label`` is the user-facing string the UI renders verbatim — never
    derive it from ``kind`` on the frontend, the backend owns the copy.
    """

    kind: RouteKind
    label: str = Field(..., min_length=1)


class ChatResponse(_Frozen):
    """Outbound POST /chat body. ``state`` is a free-form bag of metadata."""

    conversation_id: str
    reply: str
    block: Block
    state: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Route derivation
# ---------------------------------------------------------------------------


_REFUSAL_DECISIONS: frozenset[str] = frozenset(
    {"refused_unsourced", "refused_safety", "tool_failure", "denied_authz", "blocked_baa"}
)


_ROUTE_LABELS: dict[str, str] = {
    "chart": "Reading the patient record",
    "panel": "Reviewing your panel",
    "guideline": "Searching guideline evidence",
    "document": "Reading the uploaded document",
    "clarify": "Asking for clarification",
    "refusal": "Cannot ground this answer",
}

# Issue 042: panel triage failures keep advertising the panel route — the
# clinician asked about the panel, the system tried the panel route and
# failed closed, so the route label names that failure state. The kind
# stays ``panel`` so the badge styling reflects the route and the
# click-flow / source-overlay code (which dispatches on kind) is
# unaffected.
PANEL_UNAVAILABLE_LABEL = "Panel data unavailable"


def derive_route_metadata(
    *,
    workflow_id: str | None,
    decision: str | None,
    supervisor_action: str | None,
) -> RouteMetadata:
    """Map graph state to the wire ``RouteMetadata`` shape.

    Decision short-circuits (clarify, refusal) take precedence over workflow
    so a refusal that originated on a guideline-intent turn is still labeled
    as a refusal, not a guideline read. Panel triage failures (issue 042)
    are the explicit exception: a W-1 turn that fails closed keeps the
    ``panel`` route kind so the badge advertises the panel route, with a
    ``Panel data unavailable`` label naming the failure state.
    """

    if decision == "clarify":
        return RouteMetadata(kind="clarify", label=_ROUTE_LABELS["clarify"])
    if decision in _REFUSAL_DECISIONS:
        if workflow_id == "W-1":
            return RouteMetadata(kind="panel", label=PANEL_UNAVAILABLE_LABEL)
        return RouteMetadata(kind="refusal", label=_ROUTE_LABELS["refusal"])
    if workflow_id == "W-EVD" or supervisor_action == "retrieve_evidence":
        return RouteMetadata(kind="guideline", label=_ROUTE_LABELS["guideline"])
    if workflow_id == "W-DOC" or supervisor_action == "extract":
        return RouteMetadata(kind="document", label=_ROUTE_LABELS["document"])
    if workflow_id == "W-1":
        return RouteMetadata(kind="panel", label=_ROUTE_LABELS["panel"])
    return RouteMetadata(kind="chart", label=_ROUTE_LABELS["chart"])


# ---------------------------------------------------------------------------
# Citation-card mapping
# ---------------------------------------------------------------------------

# Resource-type → card. Observation needs the category (vital-signs vs
# laboratory) to disambiguate, so the helper below handles that case.
_RESOURCE_TO_CARD: dict[str, CitationCard] = {
    "MedicationRequest": "medications",
    "MedicationAdministration": "medications",
    "MedicationStatement": "medications",
    "Condition": "problems",
    "AllergyIntolerance": "allergies",
    "DocumentReference": "documents",
    "DiagnosticReport": "labs",
    "Encounter": "encounters",
    "ServiceRequest": "prescriptions",
}


def fhir_ref_to_card(
    fhir_ref: str | None,
    *,
    observation_category: str | None = None,
) -> CitationCard:
    """Map a FHIR ``ResourceType/id`` reference to a chart-card value.

    Observation rows split between ``vitals`` and ``labs`` based on the FHIR
    category code. The caller is expected to pass that category when known;
    otherwise we fall back to ``other`` rather than guess.
    """

    if not fhir_ref:
        return "other"
    # Guideline corpus refs use a ``guideline:`` prefix instead of the
    # FHIR ``ResourceType/id`` shape. Route them to a dedicated card so
    # the frontend can render source chips without forcing them through
    # the chart-card postMessage path.
    if fhir_ref.startswith("guideline:"):
        return "guideline"
    resource_type = fhir_ref.split("/", 1)[0]
    if resource_type == "Observation":
        if observation_category == "vital-signs":
            return "vitals"
        if observation_category == "laboratory":
            return "labs"
        return "other"
    return _RESOURCE_TO_CARD.get(resource_type, "other")
