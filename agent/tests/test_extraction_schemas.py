"""Unit tests for ``copilot.extraction.schemas`` (issue 002).

Strict Pydantic models are the type language for the entire ingestion
pipeline. Tests assert external behaviour:

* valid input parses cleanly and round-trips through ``model_dump`` /
  ``model_validate``;
* missing required fields are rejected;
* wrong types are rejected (no silent coercion of int <-> str on Literal
  enums);
* extra fields are rejected (``extra='forbid'``);
* boundary values (empty strings, null optionals, empty lists) behave as
  documented.

Fixture data mirrors ``example-documents/`` filenames so future end-to-end
tests can pivot on the same identifiers.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from copilot.extraction.schemas import (
    BoundingBox,
    DrawableFieldBBox,
    EvidenceChunk,
    FamilyHistoryEntry,
    FieldWithBBox,
    IntakeAllergy,
    IntakeDemographics,
    IntakeExtraction,
    IntakeMedication,
    LabExtraction,
    LabResult,
    SocialHistory,
    SourceCitation,
    filter_drawable_bboxes,
)

# ---------------------------------------------------------------------------
# SourceCitation
# ---------------------------------------------------------------------------


def _valid_source_citation_payload() -> dict[str, Any]:
    return {
        "source_type": "lab_pdf",
        "source_id": "DocumentReference/doc-1",
        "page_or_section": "page 1",
        "field_or_chunk_id": "results[0].value",
        "quote_or_value": "180 mg/dL",
    }


def test_source_citation_valid() -> None:
    sc = SourceCitation(**_valid_source_citation_payload())
    assert sc.source_type == "lab_pdf"
    assert sc.source_id == "DocumentReference/doc-1"


def test_source_citation_optional_fields_default_none() -> None:
    sc = SourceCitation(source_type="guideline", source_id="guideline:abc")
    assert sc.page_or_section is None
    assert sc.field_or_chunk_id is None
    assert sc.quote_or_value is None


def test_source_citation_rejects_unknown_source_type() -> None:
    payload = _valid_source_citation_payload()
    payload["source_type"] = "voice_memo"
    with pytest.raises(ValidationError):
        SourceCitation(**payload)


def test_source_citation_rejects_extra_field() -> None:
    payload = _valid_source_citation_payload()
    payload["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        SourceCitation(**payload)


def test_source_citation_requires_source_id() -> None:
    with pytest.raises(ValidationError):
        SourceCitation(source_type="lab_pdf")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# BoundingBox / FieldWithBBox
# ---------------------------------------------------------------------------


def test_bounding_box_valid() -> None:
    bb = BoundingBox(page=2, x=0.1, y=0.2, width=0.3, height=0.05)
    assert bb.page == 2
    assert 0 <= bb.x <= 1


@pytest.mark.parametrize(
    "field, value",
    [
        ("x", -0.1),
        ("y", 1.5),
        ("width", -0.01),
        ("height", 1.01),
    ],
)
def test_bounding_box_rejects_out_of_range_coordinates(field: str, value: float) -> None:
    payload: dict[str, Any] = {"page": 1, "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}
    payload[field] = value
    with pytest.raises(ValidationError):
        BoundingBox(**payload)


def test_bounding_box_rejects_zero_or_negative_page() -> None:
    with pytest.raises(ValidationError):
        BoundingBox(page=0, x=0.0, y=0.0, width=0.1, height=0.1)


def test_bounding_box_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        BoundingBox(page=1, x=0.0, y=0.0, width=0.1, height=0.1, extra="nope")  # type: ignore[call-arg]


def test_field_with_bbox_valid() -> None:
    fb = FieldWithBBox(
        field_path="results[0].value",
        extracted_value="180",
        matched_text="180",
        bbox=BoundingBox(page=1, x=0.1, y=0.2, width=0.05, height=0.02),
        match_confidence=0.92,
    )
    assert fb.match_confidence == pytest.approx(0.92)


@pytest.mark.parametrize("score", [-0.01, 1.5])
def test_field_with_bbox_match_confidence_bounded(score: float) -> None:
    with pytest.raises(ValidationError):
        FieldWithBBox(
            field_path="results[0].value",
            extracted_value="x",
            matched_text="x",
            bbox=BoundingBox(page=1, x=0, y=0, width=0.1, height=0.1),
            match_confidence=score,
        )


def test_field_with_bbox_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        FieldWithBBox(  # type: ignore[call-arg]
            field_path="x",
            extracted_value="x",
            matched_text="x",
            bbox=BoundingBox(page=1, x=0, y=0, width=0.1, height=0.1),
            match_confidence=0.5,
            extra="nope",
        )


# ---------------------------------------------------------------------------
# LabResult / LabExtraction
# ---------------------------------------------------------------------------


def _valid_lab_result_payload() -> dict[str, Any]:
    return {
        "test_name": "LDL Cholesterol",
        "value": "180",
        "unit": "mg/dL",
        "reference_range": "<100",
        "collection_date": "2026-04-15",
        "abnormal_flag": "high",
        "confidence": "high",
        "source_citation": _valid_source_citation_payload(),
    }


def test_lab_result_valid() -> None:
    lr = LabResult(**_valid_lab_result_payload())
    assert lr.test_name == "LDL Cholesterol"
    assert lr.confidence == "high"


@pytest.mark.parametrize("missing", ["test_name", "value", "unit"])
def test_lab_result_required_fields_rejected_when_missing(missing: str) -> None:
    payload = _valid_lab_result_payload()
    payload.pop(missing)
    with pytest.raises(ValidationError):
        LabResult(**payload)


def test_lab_result_optional_reference_range_can_be_null() -> None:
    payload = _valid_lab_result_payload()
    payload["reference_range"] = None
    lr = LabResult(**payload)
    assert lr.reference_range is None


@pytest.mark.parametrize(
    "flag",
    ["high", "low", "critical_high", "critical_low", "normal", "unknown"],
)
def test_lab_result_abnormal_flag_accepts_known_values(flag: str) -> None:
    payload = _valid_lab_result_payload()
    payload["abnormal_flag"] = flag
    LabResult(**payload)


def test_lab_result_abnormal_flag_rejects_other() -> None:
    payload = _valid_lab_result_payload()
    payload["abnormal_flag"] = "very_high"
    with pytest.raises(ValidationError):
        LabResult(**payload)


@pytest.mark.parametrize("conf", ["high", "medium", "low"])
def test_lab_result_confidence_accepts_known_values(conf: str) -> None:
    payload = _valid_lab_result_payload()
    payload["confidence"] = conf
    LabResult(**payload)


def test_lab_result_confidence_rejects_other() -> None:
    payload = _valid_lab_result_payload()
    payload["confidence"] = "very_high"
    with pytest.raises(ValidationError):
        LabResult(**payload)


def test_lab_result_rejects_extra_field() -> None:
    payload = _valid_lab_result_payload()
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        LabResult(**payload)


def test_lab_result_value_rejects_int_silent_coercion() -> None:
    """Strict mode — an integer must not silently become a string."""

    payload = _valid_lab_result_payload()
    payload["value"] = 180  # not a string
    with pytest.raises(ValidationError):
        LabResult(**payload)


def _valid_lab_extraction_payload() -> dict[str, Any]:
    return {
        "patient_name": "Maria Chen",
        "collection_date": "2026-04-15",
        "ordering_provider": "Dr. Smith",
        "lab_name": "Quest Diagnostics",
        "results": [_valid_lab_result_payload()],
        "source_document_id": "DocumentReference/doc-1",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-06T12:00:00Z",
    }


def test_lab_extraction_valid() -> None:
    le = LabExtraction(**_valid_lab_extraction_payload())
    assert len(le.results) == 1
    assert le.source_document_id == "DocumentReference/doc-1"


def test_lab_extraction_results_can_be_empty_list() -> None:
    payload = _valid_lab_extraction_payload()
    payload["results"] = []
    le = LabExtraction(**payload)
    assert le.results == []


def test_lab_extraction_optional_top_level_fields_can_be_null() -> None:
    payload = _valid_lab_extraction_payload()
    payload["patient_name"] = None
    payload["collection_date"] = None
    payload["ordering_provider"] = None
    payload["lab_name"] = None
    le = LabExtraction(**payload)
    assert le.patient_name is None


@pytest.mark.parametrize(
    "missing", ["source_document_id", "extraction_model", "extraction_timestamp"]
)
def test_lab_extraction_required_fields_rejected_when_missing(missing: str) -> None:
    payload = _valid_lab_extraction_payload()
    payload.pop(missing)
    with pytest.raises(ValidationError):
        LabExtraction(**payload)


def test_lab_extraction_rejects_extra_field() -> None:
    payload = _valid_lab_extraction_payload()
    payload["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        LabExtraction(**payload)


def test_lab_extraction_round_trip() -> None:
    payload = _valid_lab_extraction_payload()
    le = LabExtraction(**payload)
    again = LabExtraction.model_validate(le.model_dump())
    assert again == le


# ---------------------------------------------------------------------------
# IntakeExtraction sub-models
# ---------------------------------------------------------------------------


def _valid_intake_demographics_payload() -> dict[str, Any]:
    return {
        "name": "Maria Chen",
        "dob": "1968-03-15",
        "gender": "female",
        "address": "123 Oak St, Springfield",
        "phone": "555-0101",
        "emergency_contact": "John Chen, husband, 555-0102",
    }


def test_intake_demographics_valid() -> None:
    d = IntakeDemographics(**_valid_intake_demographics_payload())
    assert d.name == "Maria Chen"


def test_intake_demographics_all_optional_can_be_null() -> None:
    d = IntakeDemographics(
        name=None,
        dob=None,
        gender=None,
        address=None,
        phone=None,
        emergency_contact=None,
    )
    assert d.name is None


def test_intake_demographics_rejects_extra_field() -> None:
    payload = _valid_intake_demographics_payload()
    payload["ssn"] = "111-22-3333"
    with pytest.raises(ValidationError):
        IntakeDemographics(**payload)


def test_intake_medication_valid() -> None:
    m = IntakeMedication(name="Lisinopril", dose="10 mg", frequency="daily", prescriber="Dr. Smith")
    assert m.name == "Lisinopril"


def test_intake_medication_requires_name() -> None:
    with pytest.raises(ValidationError):
        IntakeMedication(dose="10mg", frequency="daily", prescriber=None)  # type: ignore[call-arg]


def test_intake_medication_optional_fields_can_be_null() -> None:
    m = IntakeMedication(name="Aspirin", dose=None, frequency=None, prescriber=None)
    assert m.dose is None


def test_intake_medication_rejects_extra() -> None:
    with pytest.raises(ValidationError):
        IntakeMedication(  # type: ignore[call-arg]
            name="Aspirin", dose=None, frequency=None, prescriber=None, route="po"
        )


def test_intake_allergy_valid() -> None:
    a = IntakeAllergy(substance="Penicillin", reaction="rash", severity="moderate")
    assert a.substance == "Penicillin"


def test_intake_allergy_requires_substance() -> None:
    with pytest.raises(ValidationError):
        IntakeAllergy(reaction="rash", severity="mild")  # type: ignore[call-arg]


def test_intake_allergy_optional_fields_can_be_null() -> None:
    a = IntakeAllergy(substance="latex", reaction=None, severity=None)
    assert a.reaction is None


def test_family_history_entry_valid() -> None:
    f = FamilyHistoryEntry(relation="mother", condition="Type 2 diabetes")
    assert f.relation == "mother"


def test_family_history_entry_requires_both() -> None:
    with pytest.raises(ValidationError):
        FamilyHistoryEntry(relation="mother")  # type: ignore[call-arg]


def test_social_history_valid() -> None:
    s = SocialHistory(
        smoking="never",
        alcohol="social",
        drugs="none",
        occupation="teacher",
    )
    assert s.smoking == "never"


def test_social_history_all_optional_can_be_null() -> None:
    s = SocialHistory(smoking=None, alcohol=None, drugs=None, occupation=None)
    assert s.smoking is None


# ---------------------------------------------------------------------------
# IntakeExtraction
# ---------------------------------------------------------------------------


def _valid_intake_extraction_payload() -> dict[str, Any]:
    return {
        "demographics": _valid_intake_demographics_payload(),
        "chief_concern": "shortness of breath on exertion",
        "current_medications": [
            {"name": "Lisinopril", "dose": "10 mg", "frequency": "daily", "prescriber": None}
        ],
        "allergies": [
            {"substance": "Penicillin", "reaction": "rash", "severity": "moderate"}
        ],
        "family_history": [
            {"relation": "mother", "condition": "Type 2 diabetes"}
        ],
        "social_history": {
            "smoking": "never",
            "alcohol": "social",
            "drugs": "none",
            "occupation": "teacher",
        },
        "source_citation": {
            "source_type": "intake_form",
            "source_id": "DocumentReference/doc-2",
            "page_or_section": "page 1",
            "field_or_chunk_id": None,
            "quote_or_value": None,
        },
        "source_document_id": "DocumentReference/doc-2",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-06T12:00:00Z",
    }


def test_intake_extraction_valid() -> None:
    ie = IntakeExtraction(**_valid_intake_extraction_payload())
    assert ie.chief_concern == "shortness of breath on exertion"
    assert len(ie.current_medications) == 1


def test_intake_extraction_chief_concern_required() -> None:
    payload = _valid_intake_extraction_payload()
    payload.pop("chief_concern")
    with pytest.raises(ValidationError):
        IntakeExtraction(**payload)


def test_intake_extraction_demographics_required() -> None:
    payload = _valid_intake_extraction_payload()
    payload.pop("demographics")
    with pytest.raises(ValidationError):
        IntakeExtraction(**payload)


def test_intake_extraction_medications_can_be_empty_list() -> None:
    payload = _valid_intake_extraction_payload()
    payload["current_medications"] = []
    payload["allergies"] = []
    payload["family_history"] = []
    ie = IntakeExtraction(**payload)
    assert ie.current_medications == []
    assert ie.allergies == []
    assert ie.family_history == []


def test_intake_extraction_social_history_optional() -> None:
    payload = _valid_intake_extraction_payload()
    payload["social_history"] = None
    ie = IntakeExtraction(**payload)
    assert ie.social_history is None


def test_intake_extraction_chief_concern_rejects_empty_string() -> None:
    payload = _valid_intake_extraction_payload()
    payload["chief_concern"] = ""
    with pytest.raises(ValidationError):
        IntakeExtraction(**payload)


def test_intake_extraction_rejects_extra_field() -> None:
    payload = _valid_intake_extraction_payload()
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        IntakeExtraction(**payload)


def test_intake_extraction_round_trip() -> None:
    ie = IntakeExtraction(**_valid_intake_extraction_payload())
    again = IntakeExtraction.model_validate(ie.model_dump())
    assert again == ie


def test_intake_extraction_medication_list_validates_each_entry() -> None:
    payload = _valid_intake_extraction_payload()
    payload["current_medications"] = [{"dose": "10mg"}]  # missing required name
    with pytest.raises(ValidationError):
        IntakeExtraction(**payload)


# ---------------------------------------------------------------------------
# EvidenceChunk
# ---------------------------------------------------------------------------


def _valid_evidence_chunk_payload() -> dict[str, Any]:
    return {
        "chunk_id": "jnc8-htn-001",
        "guideline_name": "JNC 8",
        "section": "BP targets in adults >=60 years",
        "page": 12,
        "text": "In the general population aged 60 years or older, initiate "
        "pharmacologic treatment to lower BP at SBP >= 150 mm Hg or DBP >= 90 mm Hg.",
        "relevance_score": 0.87,
        "source_citation": {
            "source_type": "guideline",
            "source_id": "guideline:jnc8-htn-001",
            "page_or_section": "Section 5",
            "field_or_chunk_id": "jnc8-htn-001",
            "quote_or_value": None,
        },
    }


def test_evidence_chunk_valid() -> None:
    ec = EvidenceChunk(**_valid_evidence_chunk_payload())
    assert ec.chunk_id == "jnc8-htn-001"
    assert ec.relevance_score == pytest.approx(0.87)


@pytest.mark.parametrize(
    "missing", ["chunk_id", "guideline_name", "page", "text", "relevance_score"]
)
def test_evidence_chunk_required_fields_rejected_when_missing(missing: str) -> None:
    payload = _valid_evidence_chunk_payload()
    payload.pop(missing)
    with pytest.raises(ValidationError):
        EvidenceChunk(**payload)


def test_evidence_chunk_section_optional() -> None:
    payload = _valid_evidence_chunk_payload()
    payload["section"] = None
    ec = EvidenceChunk(**payload)
    assert ec.section is None


def test_evidence_chunk_rejects_extra() -> None:
    payload = _valid_evidence_chunk_payload()
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        EvidenceChunk(**payload)


def test_evidence_chunk_text_rejects_empty_string() -> None:
    payload = _valid_evidence_chunk_payload()
    payload["text"] = ""
    with pytest.raises(ValidationError):
        EvidenceChunk(**payload)


def test_evidence_chunk_round_trip() -> None:
    ec = EvidenceChunk(**_valid_evidence_chunk_payload())
    again = EvidenceChunk.model_validate(ec.model_dump())
    assert again == ec


# ---------------------------------------------------------------------------
# DrawableFieldBBox + filter_drawable_bboxes (issue 031)
# ---------------------------------------------------------------------------


def _drawable_payload() -> dict[str, Any]:
    return {
        "field_path": "results[0].value",
        "extracted_value": "7.4",
        "matched_text": "7.4",
        "bbox": {"page": 1, "x": 0.1, "y": 0.2, "width": 0.05, "height": 0.02},
        "match_confidence": 0.95,
    }


def test_drawable_field_bbox_requires_non_null_bbox() -> None:
    payload = _drawable_payload()
    record = DrawableFieldBBox(**payload)
    assert record.bbox.page == 1
    assert 0.0 < record.bbox.width <= 1.0


def test_drawable_field_bbox_rejects_null_bbox() -> None:
    payload = _drawable_payload()
    payload["bbox"] = None
    with pytest.raises(ValidationError):
        DrawableFieldBBox(**payload)


def test_drawable_field_bbox_rejects_extra() -> None:
    payload = _drawable_payload()
    payload["unexpected"] = "x"
    with pytest.raises(ValidationError):
        DrawableFieldBBox(**payload)


def test_filter_drawable_bboxes_keeps_only_records_with_geometry() -> None:
    drawable = FieldWithBBox(
        field_path="patient_name",
        extracted_value="Eduardo Perez",
        matched_text="Eduardo Perez",
        bbox=BoundingBox(page=1, x=0.1, y=0.2, width=0.3, height=0.05),
        match_confidence=0.98,
    )
    not_drawable = FieldWithBBox(
        field_path="lab_name",
        extracted_value="LabCorp",
        matched_text="",
        bbox=None,
        match_confidence=0.0,
    )
    out = filter_drawable_bboxes([drawable, not_drawable])
    assert len(out) == 1
    assert out[0].field_path == "patient_name"
    assert out[0].bbox.page == 1
    # Every returned record carries the full set of fields the source-overlay
    # contract calls for: field path, extracted value, matched text, geometry
    # (with page), and a match confidence in [0, 1].
    assert out[0].extracted_value == "Eduardo Perez"
    assert out[0].matched_text == "Eduardo Perez"
    assert 0.0 <= out[0].match_confidence <= 1.0


def test_filter_drawable_bboxes_empty_input_is_empty() -> None:
    assert filter_drawable_bboxes([]) == []


def test_filter_drawable_bboxes_all_non_drawable_returns_empty() -> None:
    not_drawable = [
        FieldWithBBox(
            field_path=f"x{i}",
            extracted_value="v",
            matched_text="",
            bbox=None,
            match_confidence=0.0,
        )
        for i in range(3)
    ]
    assert filter_drawable_bboxes(not_drawable) == []
