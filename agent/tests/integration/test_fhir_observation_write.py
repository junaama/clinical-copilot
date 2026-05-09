"""Live FHIR Observation POST spike.

The deterministic tests in this file exercise the spike's public helpers.
The live test is opt-in via ``-m live`` because it needs a write-capable
SMART token, a deployed OpenEMR, and a real VLM extraction call.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from copilot.extraction.schemas import LabResult

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CHEN_LIPID_FIXTURE = REPO_ROOT / "evals" / "w2" / "fixtures" / "lab_chen_lipid.json"
SPIKE_MODULE = REPO_ROOT / "scripts" / "spike_fhir_observation_write.py"

_spec = importlib.util.spec_from_file_location("spike_fhir_observation_write", SPIKE_MODULE)
assert _spec is not None and _spec.loader is not None
spike = importlib.util.module_from_spec(_spec)
sys.modules["spike_fhir_observation_write"] = spike
_spec.loader.exec_module(spike)


def _fixture_lab() -> tuple[LabResult, str | None]:
    fixture = json.loads(CHEN_LIPID_FIXTURE.read_text())
    lab_data = dict(fixture["results"][0])
    lab_data["source_citation"] = {
        "source_type": "lab_pdf",
        "source_id": "DocumentReference/lab-chen-lipid",
        "page_or_section": "1",
        "field_or_chunk_id": "results[0]",
        "quote_or_value": lab_data["value"],
    }
    return LabResult.model_validate(lab_data), fixture.get("collection_date")


def test_observation_payload_maps_lab_result_to_value_quantity_and_subject() -> None:
    lab, collection_date = _fixture_lab()

    payload = spike.observation_payload_from_lab(
        lab,
        patient_id="Patient/live-patient-123",
        extraction_collection_date=collection_date,
    )

    assert payload["resourceType"] == "Observation"
    assert payload["status"] == "final"
    assert payload["subject"] == {"reference": "Patient/live-patient-123"}
    assert payload["category"][0]["coding"][0]["code"] == "laboratory"
    assert payload["code"]["text"] == lab.test_name
    assert payload["valueQuantity"]["value"] == 220
    assert payload["valueQuantity"]["unit"] == "mg/dL"
    assert payload["effectiveDateTime"] == collection_date


def test_missing_live_inputs_names_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENEMR_FHIR_TOKEN",
        "FHIR_OBSERVATION_SPIKE_PATIENT_ID",
        "E2E_PATIENT_UUID",
        "E2E_LIVE_HTTP_PATIENT_UUID",
    ):
        monkeypatch.delenv(name, raising=False)

    missing = spike.missing_live_inputs(REPO_ROOT)

    assert "ANTHROPIC_API_KEY" in missing
    assert "OPENEMR_FHIR_TOKEN" in missing
    assert "FHIR_OBSERVATION_SPIKE_PATIENT_ID or E2E_PATIENT_UUID" in missing


@pytest.mark.live
async def test_live_fhir_observation_post_round_trip() -> None:
    missing = spike.missing_live_inputs(REPO_ROOT)
    if missing:
        pytest.skip("live FHIR Observation spike missing: " + ", ".join(missing))

    report = await spike.run_spike(repo_root=REPO_ROOT)

    assert report.all_passed, report.to_text()
