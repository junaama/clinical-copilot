"""Tests for the bbox matcher (issue 005).

Strategy: build small synthetic PDFs in-memory with PyMuPDF so each test
controls the exact text and positions it cares about, plus one
integration test against a real fixture PDF from ``example-documents/``.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from pydantic import ValidationError

from copilot.extraction.bbox_matcher import match_extraction_to_bboxes
from copilot.extraction.schemas import (
    BoundingBox,
    FieldWithBBox,
    IntakeDemographics,
    IntakeExtraction,
    LabExtraction,
    LabResult,
    SourceCitation,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "example-documents"


def _pdf(*pages: list[tuple[tuple[float, float], str]]) -> bytes:
    """Build a multi-page PDF. Each page is a list of ((x, y), text)."""
    doc = fitz.open()
    for placements in pages:
        page = doc.new_page(width=612, height=792)
        for (x, y), text in placements:
            page.insert_text((x, y), text, fontsize=12)
    out = doc.tobytes()
    doc.close()
    return out


def _exact_pdf() -> bytes:
    return _pdf(
        [
            ((100, 100), "CHEN, MARGARET"),
            ((100, 150), "Hemoglobin A1C 7.2 percent"),
            ((100, 200), "LDL Cholesterol 145 mg/dL"),
        ],
    )


# ---------------------------------------------------------------------------
# Basic matching
# ---------------------------------------------------------------------------


def test_exact_value_returns_bbox_with_normalized_coords() -> None:
    pdf = _exact_pdf()
    extraction = {"patient_name": "CHEN, MARGARET"}

    [field] = match_extraction_to_bboxes(extraction, pdf)

    assert isinstance(field, FieldWithBBox)
    assert field.field_path == "patient_name"
    assert field.extracted_value == "CHEN, MARGARET"
    assert field.match_confidence == pytest.approx(1.0, abs=0.01)
    assert field.bbox is not None
    assert field.bbox.page == 1
    # Coordinates normalized to 0-1 (612x792 page).
    assert 0.0 <= field.bbox.x <= 1.0
    assert 0.0 <= field.bbox.y <= 1.0
    assert 0.0 < field.bbox.width <= 1.0
    assert 0.0 < field.bbox.height <= 1.0
    # Text inserted at (100, 100) on a 612-wide page → x ≈ 0.16.
    assert 0.10 < field.bbox.x < 0.25


def test_fuzzy_match_tolerates_ocr_typo() -> None:
    pdf = _exact_pdf()
    # OCR-style swap: "5" misread as "S" in cholesterol value.
    [field] = match_extraction_to_bboxes({"value": "LDL Cholesterol 14S mg/dL"}, pdf)

    assert field.bbox is not None, "near-match should still produce a bbox"
    assert field.match_confidence < 1.0
    assert field.match_confidence >= 0.8
    assert "LDL" in field.matched_text


def test_no_match_returns_none_bbox() -> None:
    pdf = _exact_pdf()
    extraction = {"missing": "Sodium 140 mEq/L"}

    [field] = match_extraction_to_bboxes(extraction, pdf)

    assert field.bbox is None
    assert field.match_confidence < 0.8
    # Caller can fall back to a page-level citation.


def test_below_threshold_match_returns_none_bbox_but_keeps_score() -> None:
    pdf = _exact_pdf()
    extraction = {"thing": "Completely unrelated phrase"}
    [field] = match_extraction_to_bboxes(
        extraction,
        pdf,
        similarity_threshold=0.99,  # force no match even for fuzzy hits
    )
    assert field.bbox is None


# ---------------------------------------------------------------------------
# Multi-page + non-PDF input
# ---------------------------------------------------------------------------


def test_multi_page_pdf_records_correct_page_number() -> None:
    pdf = _pdf(
        [((50, 100), "Page one anchor")],
        [((50, 100), "Sodium 140 mEq per liter")],
    )
    extraction = {"k": "Sodium 140 mEq per liter"}

    [field] = match_extraction_to_bboxes(extraction, pdf)

    assert field.bbox is not None
    assert field.bbox.page == 2


def test_png_mimetype_returns_none_bbox_for_every_field() -> None:
    extraction = {"patient_name": "CHEN, MARGARET", "result": "A1C 7.2"}
    fields = match_extraction_to_bboxes(extraction, b"\x89PNG\r\n\x1a\n", mimetype="image/png")

    assert len(fields) == 2
    for field in fields:
        assert field.bbox is None
        assert field.match_confidence == 0.0


def test_invalid_pdf_bytes_falls_back_to_no_bboxes() -> None:
    extraction = {"k": "anything"}
    [field] = match_extraction_to_bboxes(extraction, b"not a pdf at all")
    assert field.bbox is None


# ---------------------------------------------------------------------------
# Walking inputs of different shapes
# ---------------------------------------------------------------------------


def test_walks_nested_dict_with_lists_and_dotted_paths() -> None:
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {"test_name": "Hemoglobin A1C", "value": "7.2"},
        ]
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    paths = [f.field_path for f in fields]

    assert paths == ["results[0].test_name", "results[0].value"]
    # Both should be matchable in the synthesized PDF (within a single line).
    assert all(f.bbox is not None for f in fields), [
        (f.field_path, f.match_confidence) for f in fields
    ]


def test_accepts_pydantic_lab_extraction_model() -> None:
    pdf = _pdf(
        [
            ((100, 100), "CHEN, MARGARET"),
            ((100, 150), "Hemoglobin A1C 7.2 percent"),
        ],
    )
    extraction = LabExtraction(
        patient_name="CHEN, MARGARET",
        results=[
            LabResult(
                test_name="Hemoglobin A1C",
                value="7.2",
                unit="percent",
                abnormal_flag="high",
                confidence="high",
                source_citation=SourceCitation(
                    source_type="lab_pdf",
                    source_id="DocumentReference/abc",
                ),
            )
        ],
        source_document_id="DocumentReference/abc",
        extraction_model="claude-sonnet-4",
        extraction_timestamp="2026-05-06T00:00:00Z",
    )

    fields = match_extraction_to_bboxes(extraction, pdf)

    paths_with_bbox = {f.field_path for f in fields if f.bbox is not None}
    # Patient name and the lab fields should match; derived metadata
    # (extraction_model, extraction_timestamp, source_document_id,
    # abnormal_flag, confidence, source_citation.*) should not.
    assert "patient_name" in paths_with_bbox
    assert "results[0].test_name" in paths_with_bbox
    assert "results[0].value" in paths_with_bbox

    # Verify derived/metadata fields were emitted with bbox=None even
    # when they happen to be string-typed.
    by_path = {f.field_path: f for f in fields}
    assert by_path["extraction_model"].bbox is None
    assert by_path["extraction_timestamp"].bbox is None
    assert by_path["source_document_id"].bbox is None
    assert by_path["results[0].abnormal_flag"].bbox is None
    assert by_path["results[0].confidence"].bbox is None
    assert by_path["results[0].source_citation.source_id"].bbox is None


# ---------------------------------------------------------------------------
# Multi-match disambiguation
# ---------------------------------------------------------------------------


def test_multi_match_prefers_candidate_near_sibling_already_matched() -> None:
    # The value "7.2" appears twice on the page. After matching
    # "Hemoglobin A1C" near the top, a sibling-aware tie-break should
    # pull the matched "7.2" to the top occurrence rather than the bottom.
    pdf = _pdf(
        [
            ((100, 100), "Hemoglobin A1C 7.2 percent"),
            ((100, 600), "Some other metric 7.2 units"),
        ],
    )
    extraction = {
        "results": [
            {"test_name": "Hemoglobin A1C", "value": "7.2"},
        ]
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}
    name = by_path["results[0].test_name"]
    val = by_path["results[0].value"]
    assert name.bbox is not None and val.bbox is not None
    # value bbox y should be near the test_name bbox y (top of page),
    # not 500 points lower.
    assert abs(val.bbox.y - name.bbox.y) < 0.05


# ---------------------------------------------------------------------------
# Schema/path edge cases
# ---------------------------------------------------------------------------


def test_empty_string_value_returns_none_bbox() -> None:
    pdf = _exact_pdf()
    [field] = match_extraction_to_bboxes({"k": "   "}, pdf)
    assert field.bbox is None
    assert field.extracted_value == "   "


def test_returned_bboxes_validate_against_pydantic_constraints() -> None:
    pdf = _exact_pdf()
    extraction = {"patient_name": "CHEN, MARGARET"}
    [field] = match_extraction_to_bboxes(extraction, pdf)
    assert field.bbox is not None
    # Round-trip through model_validate to confirm coords are within
    # the schema's 0-1 constraints.
    BoundingBox.model_validate(field.bbox.model_dump())


def test_bbox_constraints_reject_out_of_range() -> None:
    # Sanity check on the schema itself — the matcher relies on this.
    with pytest.raises(ValidationError):
        BoundingBox(page=1, x=1.5, y=0.0, width=0.1, height=0.1)


# ---------------------------------------------------------------------------
# Integration: real fixture PDF
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (FIXTURE_DIR / "lab-results" / "p01-chen-lipid-panel.pdf").exists(),
    reason="fixture PDF not present in this checkout",
)
def test_fixture_lab_pdf_locates_patient_name() -> None:
    pdf_bytes = (FIXTURE_DIR / "lab-results" / "p01-chen-lipid-panel.pdf").read_bytes()
    extraction = IntakeExtraction(
        demographics=IntakeDemographics(name="CHEN, MARGARET"),
        chief_concern="Hyperlipidemia follow-up",
        source_citation=SourceCitation(
            source_type="lab_pdf",
            source_id="DocumentReference/p01",
        ),
        source_document_id="DocumentReference/p01",
        extraction_model="claude-sonnet-4",
        extraction_timestamp="2026-04-23T09:05:00Z",
    )

    fields = match_extraction_to_bboxes(extraction, pdf_bytes)
    by_path = {f.field_path: f for f in fields}

    name_field = by_path["demographics.name"]
    assert name_field.bbox is not None, name_field.match_confidence
    assert name_field.bbox.page == 1
    assert name_field.match_confidence >= 0.85

    chief = by_path["chief_concern"]
    # Chief concern is typed by the VLM, not present verbatim in the
    # PDF — the matcher should report no bbox.
    assert chief.bbox is None or chief.match_confidence < 0.99


# ---------------------------------------------------------------------------
# VLM-native bounding boxes (issue 056)
# ---------------------------------------------------------------------------


def test_vlm_bbox_happy_path_uses_vlm_coordinates() -> None:
    """When a valid vlm_bbox is present on a LabResult, the matcher uses it
    as the primary coordinate source instead of PyMuPDF word matching."""
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                "vlm_bbox": {"page": 1, "bbox": [0.15, 0.10, 0.85, 0.25]},
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    # Fields within the result group should use VLM-native coordinates.
    test_name = by_path["results[0].test_name"]
    assert test_name.bbox is not None
    assert test_name.bbox_source == "vlm"
    assert test_name.bbox.page == 1
    assert test_name.bbox.x == pytest.approx(0.15, abs=0.001)
    assert test_name.bbox.y == pytest.approx(0.10, abs=0.001)
    assert test_name.bbox.width == pytest.approx(0.70, abs=0.001)
    assert test_name.bbox.height == pytest.approx(0.15, abs=0.001)
    assert test_name.match_confidence == 1.0

    value_field = by_path["results[0].value"]
    assert value_field.bbox is not None
    assert value_field.bbox_source == "vlm"
    assert value_field.bbox.x == pytest.approx(0.15, abs=0.001)


def test_vlm_bbox_missing_falls_back_to_pymupdf() -> None:
    """When vlm_bbox is None or absent, the matcher falls back to PyMuPDF."""
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                "vlm_bbox": None,
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    test_name = by_path["results[0].test_name"]
    assert test_name.bbox is not None
    assert test_name.bbox_source == "pymupdf"


def test_vlm_bbox_out_of_bounds_falls_back_to_pymupdf() -> None:
    """When vlm_bbox has coordinates outside [0, 1], fall back to PyMuPDF."""
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                # x1 = 1.5 is out of bounds
                "vlm_bbox": {"page": 1, "bbox": [0.1, 0.1, 1.5, 0.3]},
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    test_name = by_path["results[0].test_name"]
    assert test_name.bbox is not None
    assert test_name.bbox_source == "pymupdf"


def test_vlm_bbox_zero_area_falls_back_to_pymupdf() -> None:
    """When vlm_bbox has zero area (x0 == x1 or y0 == y1), fall back."""
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                # x0 == x1 → zero width
                "vlm_bbox": {"page": 1, "bbox": [0.5, 0.1, 0.5, 0.3]},
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    test_name = by_path["results[0].test_name"]
    assert test_name.bbox is not None
    assert test_name.bbox_source == "pymupdf"


def test_vlm_bbox_negative_coords_falls_back_to_pymupdf() -> None:
    """When vlm_bbox has negative coordinates, fall back to PyMuPDF."""
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                "vlm_bbox": {"page": 1, "bbox": [-0.1, 0.1, 0.5, 0.3]},
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    test_name = by_path["results[0].test_name"]
    assert test_name.bbox is not None
    assert test_name.bbox_source == "pymupdf"


def test_vlm_bbox_implausibly_small_falls_back_to_pymupdf() -> None:
    """When vlm_bbox area is implausibly small, fall back to PyMuPDF."""
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                # Tiny area: 0.0001 * 0.0001 = 1e-8 < 1e-6 threshold
                "vlm_bbox": {"page": 1, "bbox": [0.5, 0.5, 0.5001, 0.5001]},
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    test_name = by_path["results[0].test_name"]
    assert test_name.bbox is not None
    assert test_name.bbox_source == "pymupdf"


def test_vlm_bbox_does_not_apply_to_top_level_fields() -> None:
    """Top-level fields (patient_name etc.) do not have VLM bboxes and
    should always use PyMuPDF matching."""
    pdf = _exact_pdf()
    extraction = {
        "patient_name": "CHEN, MARGARET",
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                "vlm_bbox": {"page": 1, "bbox": [0.15, 0.10, 0.85, 0.25]},
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    # Top-level patient_name uses PyMuPDF (no group prefix → no vlm_bbox)
    patient_name = by_path["patient_name"]
    assert patient_name.bbox is not None
    assert patient_name.bbox_source == "pymupdf"

    # Result fields use VLM
    test_name = by_path["results[0].test_name"]
    assert test_name.bbox_source == "vlm"


def test_vlm_bbox_derived_fields_still_none() -> None:
    """Derived/metadata fields (abnormal_flag, confidence, source_citation.*)
    should still produce bbox=None even when vlm_bbox is present."""
    pdf = _exact_pdf()
    extraction = {
        "results": [
            {
                "test_name": "Hemoglobin A1C",
                "value": "7.2",
                "unit": "percent",
                "abnormal_flag": "high",
                "confidence": "high",
                "source_citation": {
                    "source_type": "lab_pdf",
                    "source_id": "DocumentReference/abc",
                },
                "vlm_bbox": {"page": 1, "bbox": [0.15, 0.10, 0.85, 0.25]},
            }
        ],
        "source_document_id": "DocumentReference/abc",
        "extraction_model": "claude-sonnet-4",
        "extraction_timestamp": "2026-05-09T00:00:00Z",
    }

    fields = match_extraction_to_bboxes(extraction, pdf)
    by_path = {f.field_path: f for f in fields}

    assert by_path["results[0].abnormal_flag"].bbox is None
    assert by_path["results[0].confidence"].bbox is None
    assert by_path["results[0].source_citation.source_type"].bbox is None
