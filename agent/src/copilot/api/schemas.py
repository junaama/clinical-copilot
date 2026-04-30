"""Pydantic models that mirror ``agentforge-docs/CHAT-API-CONTRACT.md``.

Frozen, strictly-typed DTOs. The discriminated union on ``Block`` lets FastAPI
validate inbound and outbound shapes without per-handler branching, and gives
the frontend a single source of truth for codegen.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

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
    Union[TriageBlock, OvernightBlock, PlainBlock],
    Field(discriminator="kind"),
]


class ChatResponse(_Frozen):
    """Outbound POST /chat body. ``state`` is a free-form bag of metadata."""

    conversation_id: str
    reply: str
    block: Block
    state: dict[str, Any] = Field(default_factory=dict)


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
    resource_type = fhir_ref.split("/", 1)[0]
    if resource_type == "Observation":
        if observation_category == "vital-signs":
            return "vitals"
        if observation_category == "laboratory":
            return "labs"
        return "other"
    return _RESOURCE_TO_CARD.get(resource_type, "other")
