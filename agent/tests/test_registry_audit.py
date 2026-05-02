"""Registry persistence + audit ``extra.gate_decisions`` (issue 003).

Exercises the agent_node's tool-message scan and audit row shape without
spinning up the full LLM. We invoke the graph nodes' tool-result helpers
directly with synthesized ToolMessages so the assertions are deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import ToolMessage

from copilot.audit import AuditEvent, write_audit_event
from copilot.config import Settings
from copilot.graph import (
    _DENIED_DECISIONS,
    _gate_decision_for_tool_message,
    _resolved_patients_from_tool_message,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        AGENT_AUDIT_LOG_PATH=str(tmp_path / "audit.jsonl"),
    )


def _resolve_patient_payload(
    *,
    pid: str,
    given: str,
    family: str,
    dob: str,
    status: str = "resolved",
) -> str:
    return json.dumps(
        {
            "ok": True,
            "status": status,
            "patients": [
                {
                    "patient_id": pid,
                    "given_name": given,
                    "family_name": family,
                    "birth_date": dob,
                }
            ],
            "message": "",
            "sources_checked": ["CareTeam"],
            "latency_ms": 0,
        }
    )


def test_gate_decision_extracts_careteam_denied_from_tool_message() -> None:
    msg = ToolMessage(
        content=json.dumps(
            {
                "ok": False,
                "rows": [],
                "error": "careteam_denied",
                "latency_ms": 0,
            }
        ),
        tool_call_id="tc-1",
        name="get_active_medications",
    )
    assert _gate_decision_for_tool_message(msg) == "careteam_denied"


def test_gate_decision_returns_allowed_for_successful_tool_call() -> None:
    msg = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "rows": [{"fhir_ref": "MedicationRequest/m1"}],
                "error": None,
                "latency_ms": 0,
            }
        ),
        tool_call_id="tc-1",
        name="get_active_medications",
    )
    assert _gate_decision_for_tool_message(msg) == "allowed"


def test_gate_decision_collapses_non_auth_errors_to_allowed() -> None:
    """Operational errors (FHIR transport, bad arg) are NOT gate decisions
    — only the four ``AuthDecision`` values map. Everything else collapses
    to ``allowed`` because those calls *were* authorized to run; they just
    failed for non-auth reasons."""
    msg = ToolMessage(
        content=json.dumps(
            {
                "ok": False,
                "rows": [],
                "error": "transport_failure",
                "latency_ms": 0,
            }
        ),
        tool_call_id="tc-1",
        name="get_active_medications",
    )
    assert _gate_decision_for_tool_message(msg) == "allowed"


def test_resolved_patients_extracts_single_resolution() -> None:
    msg = ToolMessage(
        content=_resolve_patient_payload(
            pid="fixture-3", given="Robert", family="Hayes", dob="1949-11-04"
        ),
        tool_call_id="tc-1",
        name="resolve_patient",
    )
    out = _resolved_patients_from_tool_message(msg)
    assert "fixture-3" in out
    assert out["fixture-3"]["family_name"] == "Hayes"
    assert out["fixture-3"]["birth_date"] == "1949-11-04"


def test_resolved_patients_skips_ambiguous_status() -> None:
    """Ambiguous resolution leaves the LLM owing a follow-up to the user;
    the registry must not be populated until the disambiguation lands."""
    msg = ToolMessage(
        content=_resolve_patient_payload(
            pid="fixture-3",
            given="Robert",
            family="Hayes",
            dob="1949-11-04",
            status="ambiguous",
        ),
        tool_call_id="tc-1",
        name="resolve_patient",
    )
    assert _resolved_patients_from_tool_message(msg) == {}


def test_resolved_patients_ignores_non_resolve_tools() -> None:
    msg = ToolMessage(
        content=json.dumps(
            {"ok": True, "rows": [], "error": None, "latency_ms": 0}
        ),
        tool_call_id="tc-1",
        name="get_active_medications",
    )
    assert _resolved_patients_from_tool_message(msg) == {}


def test_audit_extra_carries_gate_decisions_and_denied_count(tmp_path: Path) -> None:
    """Audit row's ``extra`` carries the per-turn gate-decisions array and
    a denied_count summary that the dashboards can group by."""
    settings = _settings(tmp_path)
    decisions = ["allowed", "allowed", "careteam_denied", "allowed"]
    denied_count = sum(1 for d in decisions if d in _DENIED_DECISIONS)

    event = AuditEvent(
        ts="2026-05-02T00:00:00Z",
        conversation_id="conv-x",
        user_id="practitioner-dr-smith",
        patient_id="fixture-3",
        turn_index=1,
        workflow_id="W-2",
        classifier_confidence=0.9,
        decision="allow",
        regen_count=0,
        tool_call_count=4,
        fetched_ref_count=4,
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        model_provider="openai",
        model_name="gpt-4o-mini",
        extra={
            "final_response_chars": 256,
            "gate_decisions": decisions,
            "denied_count": denied_count,
        },
    )
    write_audit_event(event, settings)

    line = Path(settings.agent_audit_log_path).read_text().splitlines()[0]
    parsed = json.loads(line)
    assert parsed["extra"]["gate_decisions"] == decisions
    assert parsed["extra"]["denied_count"] == 1
