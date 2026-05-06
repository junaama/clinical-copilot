"""Eval-only Pydantic schemas for W2 fixture validation (issue 010).

The persistence-side schemas in ``copilot.extraction.schemas`` carry pipeline-
internal fields (``source_document_id``, ``extraction_model``,
``extraction_timestamp``, per-row ``source_citation``) that the VLM emits but
that aren't part of the clinical content. The eval gate wants to assert
"the structure of the document the VLM saw is well-formed" — patient name,
test rows, demographics, allergies — without coupling every case fixture
to pipeline metadata.

These eval schemas mirror the user-facing shape:

* ``LabExtractionEval``  — patient header + a list of ``LabResultEval`` rows.
* ``IntakeExtractionEval`` — demographics, chief concern, meds, allergies,
  family history, social history.

``extra='forbid'`` is preserved so a fixture with stray keys still fails the
``schema_valid`` rubric — the negative samples (``lab_005`` with ``value:
null``) continue to fire.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Mirrors persistence ``AbnormalFlag``.
AbnormalFlagEval = Literal[
    "high",
    "low",
    "critical_high",
    "critical_low",
    "normal",
    "unknown",
]

# Mirrors persistence ``ExtractionConfidence``.
ConfidenceEval = Literal["high", "medium", "low"]


class _StrictForbidEval(BaseModel):
    """Common base — ``extra=forbid`` so unexpected fields fail validation."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Lab
# ---------------------------------------------------------------------------


class LabResultEval(_StrictForbidEval):
    """One row in a fixture lab extraction.

    ``value`` is required-string. A fixture with ``value: null`` (the
    canonical negative sample) fails validation — that is the gate's
    ``schema_valid: false`` proof point.
    """

    test_name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    reference_range: str | None = None
    abnormal_flag: AbnormalFlagEval
    confidence: ConfidenceEval


class LabExtractionEval(_StrictForbidEval):
    patient_name: str | None = None
    collection_date: str | None = None
    ordering_provider: str | None = None
    lab_name: str | None = None
    results: list[LabResultEval]


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


class IntakeDemographicsEval(_StrictForbidEval):
    """Demographics block as it appears on a paper intake form."""

    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    dob: str = Field(min_length=1)
    sex: Literal["F", "M", "X", "U"] | None = None


class IntakeMedicationEval(_StrictForbidEval):
    name: str = Field(min_length=1)
    dose: str | None = None
    frequency: str | None = None


class IntakeAllergyEval(_StrictForbidEval):
    substance: str = Field(min_length=1)
    reaction: str | None = None
    severity: str | None = None


class FamilyHistoryEntryEval(_StrictForbidEval):
    relation: str = Field(min_length=1)
    condition: str = Field(min_length=1)


class SocialHistoryEval(_StrictForbidEval):
    # ``smoker`` accepts bool ("never" / "current" as boolean) OR a
    # qualitative string ("former", "occasional"). Real intake forms use
    # both — capturing only one shape would force the eval to throw away
    # information the persistence layer keeps.
    smoker: bool | str | None = None
    alcohol: str | None = None
    drugs: str | None = None
    occupation: str | None = None


class IntakeExtractionEval(_StrictForbidEval):
    demographics: IntakeDemographicsEval
    chief_concern: str = Field(min_length=1)
    current_medications: list[IntakeMedicationEval] = Field(default_factory=list)
    allergies: list[IntakeAllergyEval] = Field(default_factory=list)
    family_history: list[FamilyHistoryEntryEval] = Field(default_factory=list)
    social_history: SocialHistoryEval | None = None


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_w2_eval_schemas(register: object) -> None:
    """Register the two eval schemas under their canonical names.

    The argument is the ``register_schema`` callable from
    ``copilot.eval.w2_runner`` — passed in rather than imported here so the
    schema module stays free of runner state.
    """
    register("LabExtraction", LabExtractionEval)  # type: ignore[operator]
    register("IntakeExtraction", IntakeExtractionEval)  # type: ignore[operator]
