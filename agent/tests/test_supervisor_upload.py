"""Tests for ``copilot.supervisor.upload`` (issue 009).

The classifier prompt tells the LLM that any message starting with
``[system] Document uploaded:`` routes to W-DOC at high confidence.
The upload helper is responsible for emitting that exact sentinel —
breaking the format silently regresses the classifier and is the kind
of typo a unit test must catch before deploy.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import SystemMessage

from copilot.supervisor.upload import build_document_upload_message


def test_lab_pdf_message_carries_full_sentinel() -> None:
    msg = build_document_upload_message(
        doc_type="lab_pdf",
        filename="hba1c-2026-04.pdf",
        document_id="DocumentReference/abc-1",
        patient_id="Patient/eduardo-1",
    )
    assert isinstance(msg, SystemMessage)
    assert msg.content.startswith("[system] Document uploaded:")
    assert "lab_pdf" in msg.content
    assert "hba1c-2026-04.pdf" in msg.content
    assert "DocumentReference/abc-1" in msg.content
    assert "Patient/eduardo-1" in msg.content


def test_intake_form_message_format() -> None:
    msg = build_document_upload_message(
        doc_type="intake_form",
        filename="intake.png",
        document_id="DocumentReference/intake-9",
        patient_id="Patient/new-2",
    )
    assert "intake_form" in msg.content
    assert "intake.png" in msg.content


def test_unknown_doc_type_rejected() -> None:
    with pytest.raises(ValueError, match="unknown doc_type"):
        build_document_upload_message(
            doc_type="receipt",  # not allowed
            filename="x.pdf",
            document_id="DocumentReference/x",
            patient_id="Patient/y",
        )


def test_message_is_single_line() -> None:
    """The classifier expects a single sentinel line, not a multi-line block.

    Multi-line messages risk being split by downstream pre-processing and
    breaking the prefix match.
    """
    msg = build_document_upload_message(
        doc_type="lab_pdf",
        filename="x.pdf",
        document_id="DocumentReference/x",
        patient_id="Patient/y",
    )
    assert "\n" not in msg.content
