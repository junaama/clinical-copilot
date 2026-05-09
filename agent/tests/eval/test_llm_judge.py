"""Tests for the W2 LLM-backed factually_consistent judge."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from copilot.eval.llm_judge import factually_consistent
from copilot.eval.w2_evaluators import RubricResult


@dataclass
class _StubJudgeModel:
    responses: list[str]
    calls: int = 0
    prompts: list[list[Any]] = field(default_factory=list)

    async def ainvoke(self, messages: list[Any], **_kwargs: Any) -> AIMessage:
        self.calls += 1
        self.prompts.append(messages)
        idx = min(self.calls - 1, len(self.responses) - 1)
        return AIMessage(content=self.responses[idx])


def _cache(tmp_path: Path) -> Path:
    return tmp_path / "judge.sqlite3"


def test_factually_consistent_llm_judge_returns_known_pass(tmp_path: Path) -> None:
    model = _StubJudgeModel(
        ['{"passed": true, "details": {"reasoning": "claim matches extraction"}}']
    )

    result = factually_consistent(
        "Total cholesterol was 220 mg/dL.",
        {"results": [{"test_name": "Total Cholesterol", "value": "220", "unit": "mg/dL"}]},
        case_id="case-pass",
        cache_path=_cache(tmp_path),
        llm_factory=lambda: model,
    )

    assert isinstance(result, RubricResult)
    assert result.name == "factually_consistent"
    assert result.passed is True
    assert result.details["reasoning"] == "claim matches extraction"
    assert model.calls == 1


def test_factually_consistent_llm_judge_returns_known_fail_with_details(
    tmp_path: Path,
) -> None:
    model = _StubJudgeModel(
        ['{"passed": false, "details": {"reasoning": "LDL value is absent from fixture"}}']
    )

    result = factually_consistent(
        "LDL was 999 mg/dL.",
        {"results": [{"test_name": "Total Cholesterol", "value": "220", "unit": "mg/dL"}]},
        case_id="case-fail",
        cache_path=_cache(tmp_path),
        llm_factory=lambda: model,
    )

    assert result.passed is False
    assert result.details["reasoning"] == "LDL value is absent from fixture"
    assert model.calls == 1


def test_factually_consistent_llm_judge_short_circuits_not_applicable(
    tmp_path: Path,
) -> None:
    model = _StubJudgeModel(
        ['{"passed": false, "details": {"reasoning": "should not be called"}}']
    )

    result = factually_consistent(
        "No extraction-backed facts.",
        None,
        case_id="case-na",
        cache_path=_cache(tmp_path),
        llm_factory=lambda: model,
    )

    assert result.passed is True
    assert result.details["not_applicable"] is True
    assert model.calls == 0


def test_factually_consistent_llm_judge_uses_cache_on_unchanged_inputs(
    tmp_path: Path,
) -> None:
    model = _StubJudgeModel(
        [
            '{"passed": true, "details": {"reasoning": "first verdict"}}',
            '{"passed": false, "details": {"reasoning": "should stay cached"}}',
        ]
    )
    kwargs = {
        "case_id": "case-cache",
        "cache_path": _cache(tmp_path),
        "llm_factory": lambda: model,
    }

    first = factually_consistent("BP was 120/80.", {"bp": "120/80"}, **kwargs)
    second = factually_consistent("BP was 120/80.", {"bp": "120/80"}, **kwargs)

    assert first.passed is True
    assert second.passed is True
    assert second.details["reasoning"] == "first verdict"
    assert model.calls == 1


def test_factually_consistent_llm_judge_invalidates_cache_on_key_material_changes(
    tmp_path: Path,
) -> None:
    model = _StubJudgeModel(
        [
            '{"passed": true, "details": {"reasoning": "base"}}',
            '{"passed": true, "details": {"reasoning": "prompt changed"}}',
            '{"passed": true, "details": {"reasoning": "model changed"}}',
            '{"passed": true, "details": {"reasoning": "response changed"}}',
            '{"passed": true, "details": {"reasoning": "fixture changed"}}',
        ]
    )
    cache_path = _cache(tmp_path)

    base = factually_consistent(
        "BP was 120/80.",
        {"bp": "120/80"},
        case_id="case-invalidate",
        cache_path=cache_path,
        llm_factory=lambda: model,
    )
    prompt_changed = factually_consistent(
        "BP was 120/80.",
        {"bp": "120/80"},
        case_id="case-invalidate",
        cache_path=cache_path,
        llm_factory=lambda: model,
        prompt="Different prompt text.",
    )
    model_changed = factually_consistent(
        "BP was 120/80.",
        {"bp": "120/80"},
        case_id="case-invalidate",
        cache_path=cache_path,
        llm_factory=lambda: model,
        model_id="claude-sonnet-4-6-alt",
    )
    response_changed = factually_consistent(
        "BP was 118/76.",
        {"bp": "120/80"},
        case_id="case-invalidate",
        cache_path=cache_path,
        llm_factory=lambda: model,
    )
    fixture_changed = factually_consistent(
        "BP was 120/80.",
        {"bp": "120/80", "source": "nursing"},
        case_id="case-invalidate",
        cache_path=cache_path,
        llm_factory=lambda: model,
    )

    assert [
        base.details["reasoning"],
        prompt_changed.details["reasoning"],
        model_changed.details["reasoning"],
        response_changed.details["reasoning"],
        fixture_changed.details["reasoning"],
    ] == ["base", "prompt changed", "model changed", "response changed", "fixture changed"]
    assert model.calls == 5


def test_w2_cli_check_fails_closed_when_llm_judge_key_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from copilot.eval import w2_baseline_cli
    from copilot.eval.llm_judge import LLMJudgeConfigurationError

    def _missing_key(_repo_root: Path):
        raise LLMJudgeConfigurationError(
            "EVAL_LLM_JUDGE_ENABLED=true requires ANTHROPIC_API_KEY"
        )

    monkeypatch.setattr(w2_baseline_cli, "_run", _missing_key)

    exit_code = w2_baseline_cli.cmd_check(tmp_path)

    assert exit_code == 1
    assert "EVAL_LLM_JUDGE_ENABLED=true requires ANTHROPIC_API_KEY" in capsys.readouterr().out
