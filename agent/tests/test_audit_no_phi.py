"""Audit row PHI-scrubbing contract (issue 012).

The W2 PRD acceptance criteria require: "No raw PHI in traces (patient
referenced by ID, no document text in logs)." This test pins that
contract on the public audit row written by ``_audit`` so a future
refactor that accidentally widens ``extra`` (e.g., dumping the full
``messages`` list, the supervisor's ``input_summary`` containing a
patient name, or extracted document text) fails loudly here rather than
slipping into production.

Strategy: build a state laden with PHI-shaped strings (a fake patient
display name, a synthesized document body, a free-text user prompt)
and assert none of those strings appear anywhere in the serialized
audit row. ``patient_id`` is permitted (the row is keyed on it) but
human-readable names and free text are not.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from copilot.config import Settings
from copilot.graph import _audit

_PATIENT_NAME = "Sarah Hayes"
_DOCUMENT_TEXT = (
    "LIPID PANEL: Total cholesterol 245 mg/dL, LDL 162, HDL 38, "
    "Triglycerides 220. Patient: Hayes Sarah, DOB 1965-03-12."
)
_USER_PROMPT = "tell me about Hayes' last lipid panel"
_ASSISTANT_TEXT = (
    "Mrs. Hayes' most recent lipid panel showed elevated LDL "
    "<cite ref=\"DocumentReference/doc-1\" page=\"1\" value=\"162\"/>."
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        LLM_MODEL="gpt-4o-mini",
        AGENT_AUDIT_LOG_PATH=str(tmp_path / "audit.jsonl"),
    )


def _ai_with_usage(content: str, *, input_tokens: int, output_tokens: int) -> AIMessage:
    msg = AIMessage(content=content)
    msg.usage_metadata = {  # type: ignore[attr-defined]
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    return msg


def test_audit_row_does_not_leak_patient_name(tmp_path: Path) -> None:
    """A patient display name passed through state must never reach the audit row."""
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-phi-1",
        "user_id": "dr_smith",
        "patient_id": "p-phi-1",
        "focus_pid": "p-phi-1",
        "workflow_id": "W-2",
        "classifier_confidence": 0.9,
        "tool_results": [
            {
                "name": "get_patient_demographics",
                "args": {"patient_id": "p-phi-1"},
                "id": "1",
            },
        ],
        "fetched_refs": ["Patient/p-phi-1"],
        "messages": [
            HumanMessage(content=_USER_PROMPT),
            _ai_with_usage(_ASSISTANT_TEXT, input_tokens=100, output_tokens=50),
        ],
    }
    _audit(state, s, decision="allow", final_text=_ASSISTANT_TEXT)  # type: ignore[arg-type]

    raw = Path(s.agent_audit_log_path).read_text(encoding="utf-8")
    assert _PATIENT_NAME not in raw, "patient name leaked into audit row"
    # No surname-only leak either (e.g., from a tool arg or supervisor reasoning).
    assert "Hayes" not in raw, "patient surname leaked into audit row"


def test_audit_row_does_not_leak_document_text(tmp_path: Path) -> None:
    """Document body text from a tool result must not reach the audit row."""
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-phi-2",
        "user_id": "dr_smith",
        "patient_id": "p-phi-2",
        "focus_pid": "p-phi-2",
        "workflow_id": "W-DOC",
        "classifier_confidence": 0.95,
        "tool_results": [
            {
                "name": "extract_document",
                "args": {"patient_id": "p-phi-2", "document_id": "doc-1"},
                "id": "1",
                # Tool results often carry the full extracted document — the
                # audit row must summarize/count, not embed.
                "result": {"document_text": _DOCUMENT_TEXT},
            },
        ],
        "fetched_refs": ["DocumentReference/doc-1"],
        "messages": [
            HumanMessage(content="extract that lab"),
            _ai_with_usage("Done.", input_tokens=300, output_tokens=20),
        ],
    }
    _audit(state, s, decision="allow", final_text="Done.")  # type: ignore[arg-type]

    raw = Path(s.agent_audit_log_path).read_text(encoding="utf-8")
    assert "LIPID PANEL" not in raw, "document body text leaked"
    assert "Hayes Sarah" not in raw, "document-embedded patient name leaked"
    assert "1965-03-12" not in raw, "document-embedded DOB leaked"
    assert "245 mg/dL" not in raw, "document-embedded measurement leaked"


def test_audit_row_does_not_leak_user_prompt_or_assistant_text(tmp_path: Path) -> None:
    """Free-text user/assistant content must not be written verbatim.

    ``final_response_chars`` is the only sanctioned summary of the assistant
    text; the prompt is summarized as ``turn_index`` (count) only.
    """
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-phi-3",
        "user_id": "dr_smith",
        "patient_id": "p-phi-3",
        "focus_pid": "p-phi-3",
        "workflow_id": "W-2",
        "classifier_confidence": 0.8,
        "tool_results": [],
        "messages": [
            HumanMessage(content=_USER_PROMPT),
            _ai_with_usage(_ASSISTANT_TEXT, input_tokens=120, output_tokens=80),
        ],
    }
    _audit(state, s, decision="allow", final_text=_ASSISTANT_TEXT)  # type: ignore[arg-type]

    raw = Path(s.agent_audit_log_path).read_text(encoding="utf-8")
    parsed = json.loads(raw.splitlines()[0])
    assert parsed["extra"]["final_response_chars"] == len(_ASSISTANT_TEXT)
    # Verbatim content must not appear anywhere.
    assert _USER_PROMPT not in raw, "raw user prompt leaked into audit row"
    assert _ASSISTANT_TEXT not in raw, "raw assistant text leaked into audit row"


def test_audit_row_includes_patient_id_for_keying(tmp_path: Path) -> None:
    """patient_id is permitted (and required) for joining audit rows."""
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-phi-4",
        "user_id": "dr_smith",
        "patient_id": "p-phi-4",
        "focus_pid": "p-phi-4",
        "workflow_id": "W-2",
        "classifier_confidence": 0.9,
        "tool_results": [],
        "messages": [HumanMessage(content="brief")],
    }
    _audit(state, s, decision="allow")  # type: ignore[arg-type]
    parsed = json.loads(Path(s.agent_audit_log_path).read_text().splitlines()[0])
    assert parsed["patient_id"] == "p-phi-4"
    assert parsed["user_id"] == "dr_smith"
