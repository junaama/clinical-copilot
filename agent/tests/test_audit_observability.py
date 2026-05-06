"""Per-encounter observability fields on the audit row (issue 012).

The graph's ``_audit`` helper now scans ``state["messages"]`` for AIMessages
with ``usage_metadata`` and writes ``prompt_tokens`` / ``completion_tokens``,
``extra.cost_estimate_usd``, ``extra.cost_by_model``, and
``extra.tool_sequence``. We exercise it by building synthetic state dicts
and reading the JSONL output back.

No LLM calls — every test wires a fake AIMessage / ToolMessage.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from copilot.config import Settings
from copilot.cost_tracking import (
    aggregate_turn_cost,
    estimate_call_cost,
    estimate_embed_cost,
)
from copilot.graph import _audit, _per_call_costs, _tool_sequence


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        LLM_MODEL="gpt-4o-mini",
        AGENT_AUDIT_LOG_PATH=str(tmp_path / "audit.jsonl"),
    )


def _ai(
    *,
    content: str = "",
    tool_calls: list[dict] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str | None = None,
) -> AIMessage:
    """Build an AIMessage shaped the way LangChain's chat models populate it.

    ``response_metadata.model_name`` lets the audit row surface the actual
    model that ran (vs ``settings.llm_model``, which is the *default*).
    """
    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    if input_tokens or output_tokens:
        msg.usage_metadata = {  # type: ignore[attr-defined]
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    if model:
        msg.response_metadata = {"model_name": model}  # type: ignore[attr-defined]
    return msg


def test_per_call_costs_picks_up_usage_metadata(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    state = {
        "messages": [
            HumanMessage(content="hi"),
            _ai(input_tokens=1000, output_tokens=200, model="gpt-4o-mini"),
        ],
    }
    calls = _per_call_costs(state, s)  # type: ignore[arg-type]
    assert len(calls) == 1
    assert calls[0].input_tokens == 1000
    assert calls[0].output_tokens == 200
    assert calls[0].cost_usd is not None


def test_per_call_costs_skips_messages_without_usage(tmp_path: Path) -> None:
    """An AIMessage with no usage_metadata must not contribute zeros — that
    would inflate the rate-known-call count and skew per-model breakdowns."""
    s = _settings(tmp_path)
    state = {"messages": [_ai(content="no usage", input_tokens=0, output_tokens=0)]}
    assert _per_call_costs(state, s) == []  # type: ignore[arg-type]


def test_per_call_costs_falls_back_to_settings_model(tmp_path: Path) -> None:
    """When response_metadata doesn't echo the model, fall back to settings."""
    s = _settings(tmp_path)
    msg = _ai(input_tokens=1000, output_tokens=0)
    # No model attribute set — should pick up settings.llm_model.
    state = {"messages": [msg]}
    calls = _per_call_costs(state, s)  # type: ignore[arg-type]
    assert len(calls) == 1
    assert calls[0].model == "gpt-4o-mini"


def test_tool_sequence_from_tool_results() -> None:
    state = {
        "tool_results": [
            {"name": "resolve_patient", "args": {}, "id": "1"},
            {"name": "get_patient_demographics", "args": {}, "id": "2"},
            {"name": "get_active_problems", "args": {}, "id": "3"},
        ],
    }
    seq = _tool_sequence(state)  # type: ignore[arg-type]
    assert seq == ["resolve_patient", "get_patient_demographics", "get_active_problems"]


def test_tool_sequence_preserves_duplicates() -> None:
    """Same tool called twice (e.g. on a regen) shows up twice — caller can
    dedupe if they want, but the audit row keeps the raw signal."""
    state = {
        "tool_results": [
            {"name": "resolve_patient", "args": {}, "id": "1"},
            {"name": "resolve_patient", "args": {}, "id": "2"},
        ],
    }
    assert _tool_sequence(state) == ["resolve_patient", "resolve_patient"]  # type: ignore[arg-type]


def test_tool_sequence_falls_back_to_messages_when_state_empty() -> None:
    """When tool_results is empty (e.g. malformed tool_call), the sequence
    is still recoverable from the AIMessage tool_calls."""
    state = {
        "tool_results": [],
        "messages": [
            _ai(tool_calls=[{"name": "list_panel", "args": {}, "id": "x"}]),
        ],
    }
    assert _tool_sequence(state) == ["list_panel"]  # type: ignore[arg-type]


def test_audit_writes_cost_and_tool_sequence_into_extra(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-obs-1",
        "user_id": "dr_smith",
        "patient_id": "p-1",
        "focus_pid": "p-1",
        "workflow_id": "W-2",
        "classifier_confidence": 0.92,
        "regen_count": 0,
        "tool_results": [
            {"name": "resolve_patient", "args": {}, "id": "1"},
            {"name": "get_patient_demographics", "args": {}, "id": "2"},
        ],
        "fetched_refs": ["Patient/p-1"],
        "gate_decisions": ["allowed", "allowed"],
        "messages": [
            HumanMessage(content="brief on Hayes"),
            _ai(input_tokens=2000, output_tokens=400, model="gpt-4o-mini"),
        ],
    }

    _audit(state, s, decision="allow", final_text="...")  # type: ignore[arg-type]

    line = Path(s.agent_audit_log_path).read_text(encoding="utf-8").splitlines()[0]
    parsed = json.loads(line)
    assert parsed["prompt_tokens"] == 2000
    assert parsed["completion_tokens"] == 400
    assert parsed["tool_call_count"] == 2

    extra = parsed["extra"]
    assert extra["tool_sequence"] == [
        "resolve_patient",
        "get_patient_demographics",
    ]
    assert extra["cost_estimate_usd"] > 0
    # Hand-computed: 2000 input * 0.00015/1K + 400 output * 0.0006/1K = 0.00054.
    assert abs(extra["cost_estimate_usd"] - 0.00054) < 1e-9
    assert "gpt-4o-mini" in extra["cost_by_model"]
    assert "cost_rate_unknown_models" not in extra


def test_audit_flags_unknown_model_and_does_not_include_in_total(
    tmp_path: Path,
) -> None:
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-obs-2",
        "user_id": "dr_smith",
        "patient_id": "p-2",
        "focus_pid": "p-2",
        "workflow_id": "W-1",
        "classifier_confidence": 0.5,
        "tool_results": [],
        "messages": [
            HumanMessage(content="hi"),
            _ai(input_tokens=500, output_tokens=100, model="brand-new-model"),
        ],
    }
    _audit(state, s, decision="allow")  # type: ignore[arg-type]
    parsed = json.loads(Path(s.agent_audit_log_path).read_text().splitlines()[0])
    assert parsed["extra"]["cost_estimate_usd"] == 0.0
    assert parsed["extra"]["cost_rate_unknown_models"] == ["brand-new-model"]


def test_audit_with_no_llm_calls_writes_zero_cost(tmp_path: Path) -> None:
    """A clarify or refusal turn that didn't go through the LLM yet still
    needs a well-formed audit row."""
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-obs-3",
        "user_id": "dr_smith",
        "messages": [HumanMessage(content="who is on my panel?")],
        "tool_results": [],
    }
    _audit(state, s, decision="clarify", final_text="which patient?")  # type: ignore[arg-type]
    parsed = json.loads(Path(s.agent_audit_log_path).read_text().splitlines()[0])
    assert parsed["prompt_tokens"] == 0
    assert parsed["completion_tokens"] == 0
    assert parsed["extra"]["cost_estimate_usd"] == 0.0
    assert parsed["extra"]["tool_sequence"] == []


def test_audit_aggregates_multi_call_turn_cost(tmp_path: Path) -> None:
    """A single turn often makes multiple LLM calls (classifier + agent +
    block synthesis). The audit row should sum cost across all of them."""
    s = _settings(tmp_path)
    state = {
        "conversation_id": "conv-obs-4",
        "user_id": "dr_smith",
        "messages": [
            HumanMessage(content="brief"),
            _ai(input_tokens=100, output_tokens=20, model="gpt-4o-mini"),  # classifier
            _ai(input_tokens=2000, output_tokens=300, model="gpt-4o-mini"),  # agent
            _ai(input_tokens=800, output_tokens=200, model="gpt-4o-mini"),  # synthesis
        ],
    }
    _audit(state, s, decision="allow")  # type: ignore[arg-type]
    parsed = json.loads(Path(s.agent_audit_log_path).read_text().splitlines()[0])
    assert parsed["prompt_tokens"] == 100 + 2000 + 800
    assert parsed["completion_tokens"] == 20 + 300 + 200
    # Just confirm cost is non-zero and matches the cost_tracking module's
    # own arithmetic — we don't repeat the per-1K multiplication here because
    # test_cost_tracking.py already pins those constants.
    expected = aggregate_turn_cost(
        [
            estimate_call_cost("gpt-4o-mini", input_tokens=100, output_tokens=20),
            estimate_call_cost("gpt-4o-mini", input_tokens=2000, output_tokens=300),
            estimate_call_cost("gpt-4o-mini", input_tokens=800, output_tokens=200),
        ]
    ).total_usd
    assert abs(parsed["extra"]["cost_estimate_usd"] - expected) < 1e-9


def test_estimate_embed_cost_module_export() -> None:
    """Confirm the embed/rerank entrypoints are reachable for the future
    retrieval pipeline (issues 007/008) — once the retriever wires them in,
    its own integration tests will exercise them; this one just guards
    against an accidental rename / API break."""
    cost = estimate_embed_cost(total_tokens=1000)
    assert cost.cost_usd is not None
