"""Live round-trip test for the custom OpenEMR lab-writer module.

Run against a local or deployed OpenEMR where
``oe-module-copilot-lab-writer`` is installed and enabled::

    OPENEMR_LAB_WRITER_TOKEN=... \
    OPENEMR_LAB_WRITER_PATIENT_PID=1 \
    OPENEMR_LAB_WRITER_PATIENT_FHIR_ID=a1a7005d-850b-4567-b1e3-6f940ed71ead \
    OPENEMR_LAB_WRITER_DOCUMENT_ID=1453 \
    uv run pytest -m live -q tests/integration/test_openemr_lab_result_module_live.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.live]

DEFAULT_OPENEMR_API_BASE = "http://localhost:8300/apis/default"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _missing_env() -> list[str]:
    required = [
        "OPENEMR_LAB_WRITER_TOKEN",
        "OPENEMR_LAB_WRITER_PATIENT_PID",
        "OPENEMR_LAB_WRITER_PATIENT_FHIR_ID",
        "OPENEMR_LAB_WRITER_DOCUMENT_ID",
    ]
    return [name for name in required if not _env(name)]


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_env('OPENEMR_LAB_WRITER_TOKEN')}",
        "Content-Type": "application/json",
    }


def _resource_matches(resource: dict[str, Any], test_name: str) -> bool:
    encoded = str(resource)
    return test_name in encoded and "2345-7" in encoded


@pytest.mark.skipif(
    bool(_missing_env()),
    reason="live OpenEMR lab-writer test missing: " + ", ".join(_missing_env()),
)
async def test_live_lab_writer_post_round_trips_through_fhir_observation() -> None:
    api_base = _env("OPENEMR_LAB_WRITER_API_BASE") or DEFAULT_OPENEMR_API_BASE
    patient_pid = _env("OPENEMR_LAB_WRITER_PATIENT_PID")
    patient_fhir_id = _env("OPENEMR_LAB_WRITER_PATIENT_FHIR_ID")
    document_id = _env("OPENEMR_LAB_WRITER_DOCUMENT_ID")
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    field_path = f"live_verification.glucose_{stamp}"
    test_name = f"Codex Verification Glucose {stamp}"
    payload = {
        "results": [
            {
                "field_path": field_path,
                "source_document_id": f"DocumentReference/{document_id}",
                "test_name": test_name,
                "loinc_code": "2345-7",
                "value": "123",
                "unit": "mg/dL",
                "original_unit": "mg/dL",
                "reference_range": "70-99 mg/dL",
                "effective_datetime": "2026-05-09T16:00:00Z",
                "ordering_provider": "Codex Verification",
                "abnormal_flag": "high",
            }
        ]
    }

    async with httpx.AsyncClient(base_url=api_base, timeout=HTTP_TIMEOUT) as client:
        write = await client.post(
            f"/api/patient/{patient_pid}/lab_result",
            headers=_headers(),
            json=payload,
        )
        assert write.status_code == 200, write.text
        write_body = write.json()
        assert write_body["persistence_status"] == "succeeded"
        assert write_body["results"][0]["persistence_status"] == "created"

        repeat = await client.post(
            f"/api/patient/{patient_pid}/lab_result",
            headers=_headers(),
            json=payload,
        )
        assert repeat.status_code == 200, repeat.text
        repeat_body = repeat.json()
        assert repeat_body["persistence_status"] == "succeeded"
        assert repeat_body["results"][0]["persistence_status"] == "updated"
        assert (
            repeat_body["results"][0]["procedure_result_id"]
            == write_body["results"][0]["procedure_result_id"]
        )

        read = await client.get(
            "/fhir/Observation",
            headers=_headers(),
            params={"patient": patient_fhir_id, "category": "laboratory"},
        )
        assert read.status_code == 200, read.text

    observations = [
        entry["resource"]
        for entry in read.json().get("entry", [])
        if _resource_matches(entry.get("resource", {}), test_name)
    ]
    assert len(observations) == 1

    observation = observations[0]
    assert observation["status"] == "final"
    assert observation["valueQuantity"] == {
        "value": 123,
        "unit": "mg/dL",
        "system": "http://unitsofmeasure.org",
        "code": "mg/dL",
    }
    assert observation["interpretation"][0]["coding"][0]["code"] == "H"
    assert observation["referenceRange"][0]["low"]["value"] == 70
    assert observation["referenceRange"][0]["high"]["value"] == 99
    assert observation["derivedFrom"][0]["reference"].startswith("DocumentReference/")
