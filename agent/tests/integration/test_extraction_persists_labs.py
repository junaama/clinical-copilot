"""Integration checks for lab extraction persistence call site."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from copilot.extraction.lab_persistence import LabPersistenceItem, LabPersistenceResult
from copilot.extraction.schemas import LabExtraction, LabResult, SourceCitation


def _extraction() -> LabExtraction:
    citation = SourceCitation(
        source_type="lab_pdf",
        source_id="DocumentReference/doc-123",
    )
    return LabExtraction(
        patient_name="Eduardo Chen",
        collection_date="2026-04-15",
        ordering_provider="Dr. Smith",
        lab_name="Quest",
        source_document_id="DocumentReference/doc-123",
        extraction_model="claude-sonnet-4-6",
        extraction_timestamp="2026-05-06T12:00:00Z",
        results=[
            LabResult(
                test_name="LDL Cholesterol",
                loinc_code="13457-7",
                value="180",
                unit="mg/dL",
                reference_range="<100 mg/dL",
                collection_date=None,
                abnormal_flag="high",
                confidence="high",
                source_citation=citation,
            )
        ],
    )


@pytest.mark.asyncio
async def test_upload_lab_persistence_call_site_uses_protocol() -> None:
    from copilot import server

    calls: list[dict[str, Any]] = []

    class FakeLabPersister:
        async def persist(
            self,
            *,
            patient_id: str,
            extracted_labs: LabExtraction,
        ) -> LabPersistenceResult:
            calls.append(
                {
                    "patient_id": patient_id,
                    "value": extracted_labs.results[0].value,
                    "source_document_id": extracted_labs.source_document_id,
                }
            )
            return LabPersistenceResult(
                persistence_status="succeeded",
                results=(
                    LabPersistenceItem(
                        field_path="results.0",
                        persistence_status="created",
                        procedure_result_id=99,
                    ),
                ),
            )

    fake_app = SimpleNamespace(
        state=SimpleNamespace(lab_result_persister=FakeLabPersister())
    )

    result = await server._persist_upload_labs_to_openemr(
        fake_app,
        extraction=_extraction(),
        patient_id="123",
    )

    assert result is not None
    assert result.persistence_status == "succeeded"
    assert calls == [
        {
            "patient_id": "123",
            "value": "180",
            "source_document_id": "DocumentReference/doc-123",
        }
    ]
