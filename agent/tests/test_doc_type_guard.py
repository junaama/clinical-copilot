"""Tests for the deterministic document-type guard (issue 024).

The guard is the no-VLM safety net that catches obvious mismatches between
the clinician's selected ``doc_type`` and the file's actual content. It
combines filename heuristics with first-page text cues for PDFs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.extraction.type_guard import DetectionResult, detect_doc_type

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "example-documents"


def _read(rel: str) -> bytes:
    return (FIXTURES_ROOT / rel).read_bytes()


# ---------------------------------------------------------------------------
# Filename hints
# ---------------------------------------------------------------------------


def test_filename_lab_keyword_suggests_lab() -> None:
    result = detect_doc_type(b"%PDF-1.4\n", "p03-reyes-hba1c.pdf", "application/pdf")
    assert result.detected_type == "lab_pdf"


def test_filename_intake_keyword_suggests_intake() -> None:
    result = detect_doc_type(
        b"%PDF-1.4\n", "p04-kowalski-intake.pdf", "application/pdf"
    )
    assert result.detected_type == "intake_form"


def test_image_filename_only_yields_at_most_medium_confidence() -> None:
    """No text extraction available for images — filename is the only signal."""
    result = detect_doc_type(
        b"\x89PNG\r\n\x1a\nstub", "p03-reyes-hba1c.png", "image/png"
    )
    assert result.detected_type == "lab_pdf"
    assert result.confidence in {"medium", "low"}


def test_no_filename_no_content_returns_none() -> None:
    result = detect_doc_type(b"", "upload.bin", "application/octet-stream")
    assert result.detected_type is None
    assert result.confidence == "low"


# ---------------------------------------------------------------------------
# Real fixtures: lab PDFs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "lab-results/p01-chen-lipid-panel.pdf",
        "lab-results/p02-whitaker-cbc.pdf",
        "lab-results/p04-kowalski-cmp.pdf",
    ],
)
def test_lab_fixtures_detect_as_lab_with_high_confidence(rel: str) -> None:
    data = _read(rel)
    filename = Path(rel).name
    result = detect_doc_type(data, filename, "application/pdf")
    assert result.detected_type == "lab_pdf"
    assert result.confidence == "high"


@pytest.mark.parametrize(
    "rel",
    [
        "intake-forms/p01-chen-intake-typed.pdf",
        "intake-forms/p02-whitaker-intake.pdf",
    ],
)
def test_intake_fixtures_detect_as_intake_with_high_confidence(rel: str) -> None:
    data = _read(rel)
    filename = Path(rel).name
    result = detect_doc_type(data, filename, "application/pdf")
    assert result.detected_type == "intake_form"
    assert result.confidence == "high"


# ---------------------------------------------------------------------------
# Mismatch detection: deceptive filename, honest content wins
# ---------------------------------------------------------------------------


def test_intake_pdf_wins_over_lab_filename() -> None:
    """A filename with 'lab' but intake-shaped content still detects intake."""
    data = _read("intake-forms/p01-chen-intake-typed.pdf")
    # Pretend the user gave it a misleading lab-ish name.
    result = detect_doc_type(data, "lab-results.pdf", "application/pdf")
    assert result.detected_type == "intake_form"
    assert result.confidence == "high"


def test_lab_pdf_wins_over_intake_filename() -> None:
    data = _read("lab-results/p01-chen-lipid-panel.pdf")
    result = detect_doc_type(data, "intake-form.pdf", "application/pdf")
    assert result.detected_type == "lab_pdf"
    assert result.confidence == "high"


def test_returns_evidence_strings() -> None:
    data = _read("lab-results/p01-chen-lipid-panel.pdf")
    result: DetectionResult = detect_doc_type(
        data, "p01-chen-lipid-panel.pdf", "application/pdf"
    )
    assert len(result.evidence) > 0
    assert all(isinstance(s, str) for s in result.evidence)
