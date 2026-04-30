"""agent_audit log writes (ARCHITECTURE.md §9 step 11)."""

from __future__ import annotations

import json
from pathlib import Path

from copilot.audit import AuditEvent, now_iso, write_audit_event
from copilot.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        AGENT_AUDIT_LOG_PATH=str(tmp_path / "audit.jsonl"),
    )


def test_audit_event_round_trip(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    event = AuditEvent(
        ts=now_iso(),
        conversation_id="conv-1",
        user_id="dr_lopez",
        patient_id="fixture-1",
        turn_index=1,
        workflow_id="W-2",
        classifier_confidence=0.94,
        decision="allow",
        regen_count=0,
        tool_call_count=7,
        fetched_ref_count=12,
        latency_ms=11000,
        prompt_tokens=3500,
        completion_tokens=420,
        model_provider="openai",
        model_name="gpt-4o-mini",
    )
    write_audit_event(event, s)
    write_audit_event(event, s)

    contents = Path(s.agent_audit_log_path).read_text(encoding="utf-8").splitlines()
    assert len(contents) == 2
    parsed = json.loads(contents[0])
    assert parsed["decision"] == "allow"
    assert parsed["workflow_id"] == "W-2"
    assert parsed["patient_id"] == "fixture-1"
    assert parsed["tool_call_count"] == 7


def test_audit_failure_does_not_raise(tmp_path: Path) -> None:
    """Audit write to an unwritable path logs a warning but does not raise."""
    s = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        AGENT_AUDIT_LOG_PATH="/this/path/should/not/exist/and/is/not/writable.jsonl",
    )
    event = AuditEvent(
        ts=now_iso(),
        conversation_id="conv-x",
        user_id="u",
        patient_id="p",
        turn_index=1,
        workflow_id="W-2",
        classifier_confidence=0.0,
        decision="allow",
        regen_count=0,
        tool_call_count=0,
        fetched_ref_count=0,
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        model_provider="openai",
        model_name="gpt-4o-mini",
    )
    # Must not raise.
    write_audit_event(event, s)


def test_audit_disabled_when_path_empty(tmp_path: Path) -> None:
    s = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        AGENT_AUDIT_LOG_PATH="",
    )
    event = AuditEvent(
        ts=now_iso(),
        conversation_id="conv-x",
        user_id="u",
        patient_id="p",
        turn_index=1,
        workflow_id="W-2",
        classifier_confidence=0.0,
        decision="allow",
        regen_count=0,
        tool_call_count=0,
        fetched_ref_count=0,
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        model_provider="openai",
        model_name="gpt-4o-mini",
    )
    write_audit_event(event, s)
    # No file should have been created anywhere.
