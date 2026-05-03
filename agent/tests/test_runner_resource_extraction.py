"""Runner-internal helpers for FaithfulnessJudge wiring (issue 011).

The judge needs the actual FHIR resource bodies (not just the refs) so it
can compare each citation against what the agent retrieved. The runner
extracts these from ToolMessage content via two pure helpers tested here.
"""

from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from copilot.eval.runner import (
    _extract_resources_from_tool_message,
    _walk_for_rows,
)


def test_walk_for_rows_extracts_simple_payload() -> None:
    payload = {
        "ok": True,
        "rows": [
            {
                "fhir_ref": "Observation/obs-bp-1",
                "resource_type": "Observation",
                "fields": {"value": "90/60"},
            },
            {
                "fhir_ref": "Observation/obs-bp-2",
                "resource_type": "Observation",
                "fields": {"value": "180/110"},
            },
        ],
    }
    rows = dict(_walk_for_rows(payload))
    assert set(rows.keys()) == {"Observation/obs-bp-1", "Observation/obs-bp-2"}
    # The fhir_ref key itself is dropped from each body so it doesn't echo
    # back into the judge prompt as noise.
    assert "fhir_ref" not in rows["Observation/obs-bp-1"]
    assert rows["Observation/obs-bp-1"]["fields"]["value"] == "90/60"


def test_walk_for_rows_handles_nested_composite_payloads() -> None:
    """Composite tools (per_patient_brief etc.) nest results several layers
    deep — the walker must reach them."""
    payload = {
        "brief": {
            "vitals": {
                "rows": [
                    {"fhir_ref": "Observation/v1", "value": "98.6"},
                ]
            },
            "meds": [
                {"rows": [{"fhir_ref": "MedicationRequest/m1", "name": "lisinopril"}]}
            ],
        }
    }
    rows = dict(_walk_for_rows(payload))
    assert set(rows.keys()) == {"Observation/v1", "MedicationRequest/m1"}


def test_extract_resources_from_tool_message_round_trip() -> None:
    payload = {
        "ok": True,
        "rows": [
            {"fhir_ref": "Observation/obs-1", "fields": {"x": 1}},
            {"fhir_ref": "Condition/cond-1", "fields": {"code": "A"}},
        ],
    }
    msg = ToolMessage(
        content=json.dumps(payload),
        tool_call_id="tc-1",
    )
    extracted = _extract_resources_from_tool_message(msg)
    assert set(extracted.keys()) == {"Observation/obs-1", "Condition/cond-1"}


def test_extract_resources_falls_back_to_regex_on_non_json() -> None:
    """Older / non-standard tool outputs may not be JSON. The regex
    fallback still pulls refs (with empty bodies) so citation_resolution
    keeps working even if the judge can't ground them."""
    msg = ToolMessage(
        content='garbled "fhir_ref": "Observation/obs-x" tail not-json',
        tool_call_id="tc-1",
    )
    extracted = _extract_resources_from_tool_message(msg)
    assert "Observation/obs-x" in extracted
    assert extracted["Observation/obs-x"] == {}


def test_extract_resources_empty_content_returns_empty_dict() -> None:
    msg = ToolMessage(content="", tool_call_id="tc-1")
    assert _extract_resources_from_tool_message(msg) == {}
