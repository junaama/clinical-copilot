"""LLM-backed W2 rubric judges.

The prompts in this module are prompt-as-code: each rubric keeps its full
grading instruction beside the parser and cache key material that make its
verdict reproducible. The judge asks for strict JSON only, fails closed on
malformed output, and records prompt/model/schema hashes in the SQLite cache so
prompt or contract edits invalidate old verdicts automatically.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr

from copilot.config import get_settings
from copilot.llm import build_chat_model

from .w2_evaluators import RubricResult

FACTUALLY_CONSISTENT_RUBRIC = "factually_consistent"
JUDGE_MODEL_ID = "claude-sonnet-4-6"
JUDGE_SCHEMA_VERSION = "factually_consistent.v1"

# Prompting approach: keep the judge narrow, deterministic, and JSON-only.
# Citation form is deliberately ignored here because separate W2 rubrics own
# citation presence; this rubric only compares response facts to extraction.
FACTUALLY_CONSISTENT_PROMPT = """
You are a strict clinical evaluation judge for the W2 factually_consistent
rubric. Compare the assistant response against the fixture extraction only.

Pass when every factual clinical claim in the response is supported by the
fixture extraction. Fail when the response fabricates a value, changes a unit,
changes a status, attributes a fact to the wrong entity, or asserts a clinical
fact absent from the fixture extraction.

Ignore citation formatting. Other rubrics grade whether citations are present.
Focus only on factual consistency with the provided extraction.

Reply ONLY with a JSON object:
{"passed": true|false, "details": {"reasoning": "<short explanation>"}}

The details object must be non-empty. Keep reasoning under 240 characters.
""".strip()


class LLMJudgeConfigurationError(RuntimeError):
    """Raised when LLM judging is enabled but cannot be constructed."""


def factually_consistent(
    response_text: str,
    fixture_extraction: dict[str, Any] | None,
    *,
    case_id: str = "",
    cache_path: Path | None = None,
    llm_factory: Callable[[], Any] | None = None,
    prompt: str = FACTUALLY_CONSISTENT_PROMPT,
    model_id: str = JUDGE_MODEL_ID,
    judge_schema_version: str = JUDGE_SCHEMA_VERSION,
) -> RubricResult:
    """Judge whether ``response_text`` is supported by ``fixture_extraction``.

    The first two parameters mirror the regex evaluator so the W2 runner can
    swap this in for the ``factually_consistent`` rubric only. Cases without
    fixture extraction are not applicable and short-circuit without a model
    call.
    """
    if fixture_extraction is None:
        return RubricResult(
            name=FACTUALLY_CONSISTENT_RUBRIC,
            passed=True,
            details={"not_applicable": True},
        )

    resolved_cache_path = cache_path or _default_cache_path()
    key = _cache_key(
        case_id=case_id,
        response_text=response_text,
        fixture_extraction=fixture_extraction,
        prompt=prompt,
        model_id=model_id,
        judge_schema_version=judge_schema_version,
    )
    cached = _read_cached_verdict(resolved_cache_path, key)
    if cached is not None:
        return cached

    factory = llm_factory or build_default_judge_factory(model_id=model_id)
    raw = _run_async(_call_judge(factory, response_text, fixture_extraction, prompt))
    result = _parse_judge_response(raw)
    _write_cached_verdict(resolved_cache_path, key, result, raw)
    return result


def build_default_judge_factory(*, model_id: str = JUDGE_MODEL_ID) -> Callable[[], Any]:
    """Build a Sonnet judge through ``copilot.llm.build_chat_model``."""
    settings = get_settings()
    api_key = settings.anthropic_api_key.get_secret_value()
    if not api_key:
        raise LLMJudgeConfigurationError(
            "EVAL_LLM_JUDGE_ENABLED=true requires ANTHROPIC_API_KEY for "
            "the factually_consistent LLM judge"
        )
    judge_settings = settings.model_copy(
        update={
            "llm_provider": "anthropic",
            "llm_model": model_id,
            "anthropic_api_key": SecretStr(api_key),
        }
    )

    def _factory() -> Any:
        return build_chat_model(judge_settings, temperature=0.0)

    return _factory


def ensure_llm_judge_ready() -> None:
    """Fail closed early when the W2 gate enables LLM judging without a key."""
    if not get_settings().anthropic_api_key.get_secret_value():
        raise LLMJudgeConfigurationError(
            "EVAL_LLM_JUDGE_ENABLED=true requires ANTHROPIC_API_KEY for "
            "the factually_consistent LLM judge"
        )


async def _call_judge(
    llm_factory: Callable[[], Any],
    response_text: str,
    fixture_extraction: dict[str, Any],
    prompt: str,
) -> str:
    model = llm_factory()
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=_build_user_prompt(response_text, fixture_extraction)),
    ]
    reply = await model.ainvoke(messages)
    raw_content = getattr(reply, "content", "")
    if not isinstance(raw_content, str):
        raw_content = str(raw_content or "")
    return raw_content


def _build_user_prompt(response_text: str, fixture_extraction: dict[str, Any]) -> str:
    extraction_json = json.dumps(
        fixture_extraction,
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
        default=str,
    )
    return (
        "ASSISTANT RESPONSE:\n"
        f"{response_text or ''}\n\n"
        "FIXTURE EXTRACTION:\n"
        f"{extraction_json}\n\n"
        "Return the JSON verdict now."
    )


def _parse_judge_response(raw: str) -> RubricResult:
    payload, parse_error = _extract_json_object(raw)
    if parse_error is not None:
        return RubricResult(
            name=FACTUALLY_CONSISTENT_RUBRIC,
            passed=False,
            details={"error": parse_error},
        )

    passed = bool(payload.get("passed"))
    raw_details = payload.get("details")
    details = raw_details if isinstance(raw_details, dict) else {}
    if not details:
        details = {"reasoning": str(payload.get("reasoning") or "judge returned no details")}
    return RubricResult(
        name=FACTUALLY_CONSISTENT_RUBRIC,
        passed=passed,
        details=details,
    )


def _extract_json_object(raw: str) -> tuple[dict[str, Any], str | None]:
    if not raw:
        return {}, "judge returned empty content"
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return {}, f"judge output not parseable: {cleaned[:120]!r}"
    candidate = cleaned[first : last + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return {}, f"judge JSON decode error: {exc}; raw={candidate[:120]!r}"
    if not isinstance(payload, dict):
        return {}, "judge output was not a JSON object"
    return payload, None


def _cache_key(
    *,
    case_id: str,
    response_text: str,
    fixture_extraction: dict[str, Any],
    prompt: str,
    model_id: str,
    judge_schema_version: str,
) -> dict[str, str]:
    return {
        "rubric": FACTUALLY_CONSISTENT_RUBRIC,
        "case_id": case_id,
        "response_hash": _sha256_text(response_text or ""),
        "fixture_extraction_hash": _sha256_json(fixture_extraction),
        "prompt_hash": _sha256_text(prompt),
        "model_id": model_id,
        "judge_schema_version": judge_schema_version,
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256_text(encoded)


def _default_cache_path() -> Path:
    raw = os.environ.get("EVAL_LLM_JUDGE_CACHE")
    if raw:
        return Path(raw)
    return Path.cwd() / ".cache" / "eval_llm_judge.sqlite3"


def _connect(cache_path: Path) -> sqlite3.Connection:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cache_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_judge_verdicts (
            rubric TEXT NOT NULL,
            case_id TEXT NOT NULL,
            response_hash TEXT NOT NULL,
            fixture_extraction_hash TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            model_id TEXT NOT NULL,
            judge_schema_version TEXT NOT NULL,
            passed INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            raw_response TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                rubric,
                case_id,
                response_hash,
                fixture_extraction_hash,
                prompt_hash,
                model_id,
                judge_schema_version
            )
        )
        """
    )
    return conn


def _read_cached_verdict(
    cache_path: Path,
    key: dict[str, str],
) -> RubricResult | None:
    with _connect(cache_path) as conn:
        row = conn.execute(
            """
            SELECT passed, details_json
            FROM llm_judge_verdicts
            WHERE rubric = :rubric
              AND case_id = :case_id
              AND response_hash = :response_hash
              AND fixture_extraction_hash = :fixture_extraction_hash
              AND prompt_hash = :prompt_hash
              AND model_id = :model_id
              AND judge_schema_version = :judge_schema_version
            """,
            key,
        ).fetchone()
    if row is None:
        return None
    return RubricResult(
        name=FACTUALLY_CONSISTENT_RUBRIC,
        passed=bool(row[0]),
        details=json.loads(str(row[1])),
    )


def _write_cached_verdict(
    cache_path: Path,
    key: dict[str, str],
    result: RubricResult,
    raw_response: str,
) -> None:
    params: dict[str, Any] = {
        **key,
        "passed": int(result.passed),
        "details_json": json.dumps(result.details, ensure_ascii=True, sort_keys=True),
        "raw_response": raw_response,
    }
    with _connect(cache_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO llm_judge_verdicts (
                rubric,
                case_id,
                response_hash,
                fixture_extraction_hash,
                prompt_hash,
                model_id,
                judge_schema_version,
                passed,
                details_json,
                raw_response
            ) VALUES (
                :rubric,
                :case_id,
                :response_hash,
                :fixture_extraction_hash,
                :prompt_hash,
                :model_id,
                :judge_schema_version,
                :passed,
                :details_json,
                :raw_response
            )
            """,
            params,
        )


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error["value"]
    return result.get("value")
