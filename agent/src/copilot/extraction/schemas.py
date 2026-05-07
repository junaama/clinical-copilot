"""Pydantic models for the document extraction + retrieval pipelines.

These are pure data models (no I/O, no LLM, no DB). They are the shared
type language for VLM extraction (lab PDFs and intake forms), bounding-box
matching, and guideline retrieval. Issue 002 owns this module; issue 005
extends ``BoundingBox`` / ``FieldWithBBox`` usage for bbox matching.

Design choices:

* ``extra='forbid'`` on every model so the VLM can't smuggle unknown fields
  past validation. Persistence and citation depend on the schema being a
  closed shape.
* ``strict=True`` for the extraction-output models so an integer ``180``
  doesn't silently coerce to the string ``"180"``. Lab values lose meaning
  when type-coerced (precision, leading zeros).
* ``Literal`` enums for ``abnormal_flag``, ``confidence``, and
  ``source_type`` so each draws from a closed set the supervisor and UI
  can switch on exhaustively.
* Optional top-level fields (``patient_name``, ``collection_date``, …) use
  ``str | None`` because real intake forms and lab PDFs frequently omit
  them; rejecting an extraction over a missing patient name would defeat
  the point.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Closed set of citation sources. Anything outside this list is a programming
# error, not a runtime input — the supervisor wires sources by hand.
SourceType = Literal["lab_pdf", "intake_form", "guideline", "fhir_resource"]

# Lab-result clinical interpretation. ``unknown`` is the explicit fallback
# when the document neither labels nor implies an interpretation, rather
# than ``None`` (which would mix "absent" with "normal").
AbnormalFlag = Literal[
    "high",
    "low",
    "critical_high",
    "critical_low",
    "normal",
    "unknown",
]

# Per-field VLM confidence. The supervisor surfaces ``low`` values to the
# clinician with a "double-check the source" badge rather than asserting
# them as fact.
ExtractionConfidence = Literal["high", "medium", "low"]


class _StrictForbid(BaseModel):
    """Common base — strict types and no extra fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


# ---------------------------------------------------------------------------
# Source citation + bounding box
# ---------------------------------------------------------------------------


class SourceCitation(_StrictForbid):
    """Where an extracted value came from.

    The shape is unified across all four source types so verifier code can
    handle citations uniformly. ``page_or_section``, ``field_or_chunk_id``,
    and ``quote_or_value`` are all optional because not every source emits
    every field (a guideline chunk has no ``field_or_chunk_id`` other than
    its own ``source_id``; a typed-text intake form may not pin a quote).
    """

    source_type: SourceType
    source_id: str = Field(min_length=1)
    page_or_section: str | None = None
    field_or_chunk_id: str | None = None
    quote_or_value: str | None = None


class BoundingBox(_StrictForbid):
    """Normalized rectangle on a page (coords in 0-1 range, top-left origin).

    Coordinates are normalized so the UI doesn't have to know the source
    PDF's pixel dimensions to draw the overlay. ``page`` is 1-indexed to
    match how PyMuPDF and human readers count pages.
    """

    page: int = Field(ge=1, description="1-indexed page number.")
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class FieldWithBBox(_StrictForbid):
    """An extracted value paired with its location in the source document.

    ``bbox`` is ``None`` when the bbox matcher could not find the extracted
    value in the OCR spans (handwritten text, poor scan, scanned image
    without text layer). The caller falls back to a page-level citation.
    """

    field_path: str = Field(min_length=1, description="Dotted path into the extraction object.")
    extracted_value: str = Field(description="The value the VLM extracted (may be empty).")
    matched_text: str = Field(description="The OCR span text that was matched against.")
    bbox: BoundingBox | None = Field(
        default=None,
        description="None when no fuzzy match was found — caller falls back to a page citation.",
    )
    match_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="String similarity score (1.0 = exact, 0.0 = no overlap).",
    )


class DrawableFieldBBox(_StrictForbid):
    """``FieldWithBBox`` narrowed to records the source-overlay can draw.

    Same shape as ``FieldWithBBox`` but ``bbox`` is non-null by construction.
    The upload response uses this type so the frontend never has to branch
    on ``bbox === null`` when rendering the source-grounding overlay —
    records without geometry are filtered at the response boundary.
    """

    field_path: str = Field(min_length=1, description="Dotted path into the extraction object.")
    extracted_value: str
    matched_text: str
    bbox: BoundingBox
    match_confidence: float = Field(ge=0.0, le=1.0)


def filter_drawable_bboxes(
    bboxes: list[FieldWithBBox],
) -> list[DrawableFieldBBox]:
    """Keep only records the source-grounding overlay can actually draw.

    The bbox matcher emits one ``FieldWithBBox`` per string-leaf in an
    extraction; entries whose value the matcher could not locate in the
    source carry ``bbox=None``. The upload endpoint filters those out so
    the response only carries records the UI can render.
    """

    return [
        DrawableFieldBBox(
            field_path=b.field_path,
            extracted_value=b.extracted_value,
            matched_text=b.matched_text,
            bbox=b.bbox,
            match_confidence=b.match_confidence,
        )
        for b in bboxes
        if b.bbox is not None
    ]


# ---------------------------------------------------------------------------
# Lab extraction
# ---------------------------------------------------------------------------


class LabResult(_StrictForbid):
    """One row in a lab PDF — a single test name, value, unit, interpretation.

    ``value`` is ``str`` because lab values mix integers ("4"), floats
    ("4.2"), and qualitative results ("positive", "trace"). The VLM is
    instructed to emit the value as it appears on the document.
    """

    test_name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    reference_range: str | None = None
    collection_date: str | None = None
    abnormal_flag: AbnormalFlag
    confidence: ExtractionConfidence
    source_citation: SourceCitation


class LabExtraction(_StrictForbid):
    """Validated VLM output for a lab PDF.

    Top-level identifiers (patient name, collection date, ordering provider,
    lab name) are optional because intake forms and quick captures may not
    include them. ``results`` may be empty for documents that don't contain
    structured tests (e.g. radiology reports mistyped as labs) — the
    persistence layer handles the empty case.
    """

    patient_name: str | None = None
    collection_date: str | None = None
    ordering_provider: str | None = None
    lab_name: str | None = None
    results: list[LabResult] = Field(default_factory=list)
    source_document_id: str = Field(min_length=1, description="DocumentReference/{id}.")
    extraction_model: str = Field(min_length=1)
    extraction_timestamp: str = Field(min_length=1, description="ISO 8601.")


# ---------------------------------------------------------------------------
# Intake-form extraction
# ---------------------------------------------------------------------------


class IntakeDemographics(_StrictForbid):
    """Patient-identifying fields from an intake form.

    Every field is optional because intake forms vary wildly in
    completeness; the persistence layer maps populated fields onto
    ``PUT /fhir/r4/Patient/{id}`` and ignores ``None`` values.
    """

    name: str | None = None
    dob: str | None = None
    gender: str | None = None
    address: str | None = None
    phone: str | None = None
    emergency_contact: str | None = None


class IntakeMedication(_StrictForbid):
    """One medication row from an intake form.

    ``name`` is required because the persistence layer can't write
    ``POST /api/patient/:pid/medication`` without it. Dose / frequency /
    prescriber are commonly blank on patient-completed forms.
    """

    name: str = Field(min_length=1)
    dose: str | None = None
    frequency: str | None = None
    prescriber: str | None = None


class IntakeAllergy(_StrictForbid):
    """One allergy row from an intake form.

    ``substance`` is required for the same reason ``IntakeMedication.name``
    is — without it, ``POST /api/patient/:pid/allergy`` has nothing to
    persist.
    """

    substance: str = Field(min_length=1)
    reaction: str | None = None
    severity: str | None = None


class FamilyHistoryEntry(_StrictForbid):
    """One row from the family-history section.

    Both fields are required: a relation without a condition (or vice
    versa) carries no clinical signal and shouldn't survive validation.
    """

    relation: str = Field(min_length=1)
    condition: str = Field(min_length=1)


class SocialHistory(_StrictForbid):
    """Social-history section.

    All fields are optional — patients commonly skip parts of this section.
    """

    smoking: str | None = None
    alcohol: str | None = None
    drugs: str | None = None
    occupation: str | None = None


class IntakeExtraction(_StrictForbid):
    """Validated VLM output for an intake form.

    ``demographics`` and ``chief_concern`` are required — the form is
    pointless without at least the reason the patient is presenting.
    Medications / allergies / family history are required keys but the
    lists may be empty (the patient has no medications etc.).
    ``social_history`` is fully optional.
    """

    demographics: IntakeDemographics
    chief_concern: str = Field(min_length=1)
    current_medications: list[IntakeMedication] = Field(default_factory=list)
    allergies: list[IntakeAllergy] = Field(default_factory=list)
    family_history: list[FamilyHistoryEntry] = Field(default_factory=list)
    social_history: SocialHistory | None = None
    source_citation: SourceCitation
    source_document_id: str = Field(min_length=1, description="DocumentReference/{id}.")
    extraction_model: str = Field(min_length=1)
    extraction_timestamp: str = Field(min_length=1, description="ISO 8601.")


# ---------------------------------------------------------------------------
# Evidence retrieval
# ---------------------------------------------------------------------------


class EvidenceChunk(_StrictForbid):
    """One chunk from the guideline corpus, ranked by the retriever.

    Returned by the hybrid retriever (issue 008) to the synthesis step.
    ``relevance_score`` is the rerank score when Cohere rerank is
    available, otherwise the RRF score from the pgvector / tsvector hybrid
    query. ``source_citation`` carries ``source_type='guideline'`` so the
    verifier can resolve ``<cite ref="guideline:{chunk_id}"/>`` references.
    """

    chunk_id: str = Field(min_length=1)
    guideline_name: str = Field(min_length=1)
    section: str | None = None
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    relevance_score: float
    source_citation: SourceCitation
