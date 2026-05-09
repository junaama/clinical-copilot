"""Tests for lab-result persistence through the OpenEMR module backend."""

from __future__ import annotations

from typing import Any

import pytest

from copilot.extraction.schemas import LabExtraction, LabResult, SourceCitation


class _FakeStandardClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def create_lab_result(
        self,
        patient_id: str,
        payload: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None, str | None, int]:
        self.calls.append((patient_id, payload))
        return True, {
            "persistence_status": "succeeded",
            "results": [
                {
                    "field_path": "results.0",
                    "persistence_status": "created",
                    "procedure_result_id": 55,
                }
            ],
        }, None, 12


def _lab_extraction() -> LabExtraction:
    citation = SourceCitation(
        source_type="lab_pdf",
        source_id="DocumentReference/doc-77",
    )
    return LabExtraction(
        patient_name="Eduardo Chen",
        collection_date="2026-04-15T10:30:00-04:00",
        ordering_provider="Dr. Smith",
        lab_name="Quest",
        source_document_id="DocumentReference/doc-77",
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
async def test_openemr_module_persister_maps_lab_payload() -> None:
    from copilot.extraction.lab_persistence import OpenEmrLabResultPersister

    client = _FakeStandardClient()
    persister = OpenEmrLabResultPersister(client)

    result = await persister.persist(
        patient_id="Patient/123",
        extracted_labs=_lab_extraction(),
    )

    assert result.persistence_status == "succeeded"
    assert result.results[0].field_path == "results.0"
    assert client.calls == [
        (
            "Patient/123",
            {
                "results": [
                    {
                        "field_path": "results.0",
                        "source_document_id": "DocumentReference/doc-77",
                        "loinc_code": "13457-7",
                        "test_name": "LDL Cholesterol",
                        "value": "180",
                        "unit": "mg/dL",
                        "original_unit": "mg/dL",
                        "reference_range": "<100 mg/dL",
                        "effective_datetime": "2026-04-15T10:30:00-04:00",
                        "ordering_provider": "Dr. Smith",
                        "abnormal_flag": "high",
                    }
                ]
            },
        )
    ]


@pytest.mark.asyncio
async def test_openemr_module_persister_returns_per_result_failure() -> None:
    from copilot.extraction.lab_persistence import OpenEmrLabResultPersister

    class FailingClient(_FakeStandardClient):
        async def create_lab_result(
            self,
            patient_id: str,
            payload: dict[str, Any],
        ) -> tuple[bool, dict[str, Any] | None, str | None, int]:
            self.calls.append((patient_id, payload))
            return False, None, "http_500", 8

    persister = OpenEmrLabResultPersister(FailingClient())

    result = await persister.persist(
        patient_id="123",
        extracted_labs=_lab_extraction(),
    )

    assert result.persistence_status == "failed"
    assert result.results[0].field_path == "results.0"
    assert result.results[0].persistence_status == "failed"
    assert result.results[0].error == {
        "code": "openemr_module_write_failed",
        "message": "http_500",
    }
