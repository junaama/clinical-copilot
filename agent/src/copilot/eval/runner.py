"""Eval runner — execute one case end-to-end and score it.

The runner is a thin orchestrator:
1. Build the agent graph for the case's persona/patient context.
2. Invoke the graph with the case's input message.
3. Extract response text, citations, tool calls, latency, and cost.
4. Run all evaluators against the result.
5. Aggregate into a ``CaseResult`` and (optionally) push to Langfuse.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ..config import Settings, get_settings
from ..graph import build_graph
from ..observability import get_callback_handler
from . import evaluators
from .case import Case, CaseResult
from .langfuse_client import LangfuseClient

_log = logging.getLogger(__name__)


async def run_case(
    case: Case,
    settings: Settings | None = None,
    langfuse: LangfuseClient | None = None,
) -> CaseResult:
    """Execute a single case and return its scored result."""
    settings = settings or get_settings()

    # Build the full graph (agent → verifier) so eval runs exercise the
    # verifier's citation-resolution check, not just the bare agent.
    graph = build_graph(settings)
    # Honor an explicit empty string ("") as "no active patient" — that's
    # the panel-spanning shape (UC-1 triage, UC-10 med-safety). Default to
    # fixture-1 only when the case doesn't specify any patient context at all.
    patient_id = case.patient_id if case.patient_id is not None else "fixture-1"

    # Replay prior turns (multi-turn cases) before the current message.
    # The system prompt is owned by the graph's agent_node.
    history: list[Any] = []
    for turn in case.prior_turns:
        role = turn.get("role")
        content = turn.get("content", "")
        if role == "user":
            history.append(HumanMessage(content=content))
        elif role == "assistant":
            history.append(AIMessage(content=content))
        # Tool turns are reconstructed by re-running, not replayed verbatim.

    history.append(HumanMessage(content=case.message))

    error: str | None = None
    response_text = ""
    citations: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    fetched_refs: set[str] = set()
    prompt_tokens = 0
    completion_tokens = 0

    start = time.monotonic()
    state_decision: str | None = None
    state_fetched: set[str] = set()
    state_workflow: str | None = None
    state_confidence: float | None = None
    invoke_config: dict[str, Any] = {"configurable": {"thread_id": f"eval-{case.id}"}}
    handler = get_callback_handler(settings)
    if handler is not None:
        invoke_config["callbacks"] = [handler]
    try:
        result = await graph.ainvoke(
            {
                "messages": history,
                "patient_id": patient_id,
                "conversation_id": f"eval-{case.id}",
                "user_id": case.user_id or "eval",
            },
            config=invoke_config,
        )
        state_decision = result.get("decision")
        state_fetched = set(result.get("fetched_refs") or [])
        state_workflow = result.get("workflow_id")
        state_confidence = result.get("classifier_confidence")
    except Exception as exc:  # noqa: BLE001 — eval should never bring down the runner
        error = f"{type(exc).__name__}: {exc}"
        _log.exception("case %s raised", case.id)
        result = {}
    latency_ms = int((time.monotonic() - start) * 1000)

    if error is None:
        response_text, citations, tool_calls, fetched_refs, prompt_tokens, completion_tokens = (
            _extract_observed(result)
        )
        # The parent graph stores fetched_refs and tool_results as state because
        # the agent's sub-graph hides ToolMessages from the parent. Merge them
        # in so eval scoring sees a complete picture.
        fetched_refs = fetched_refs | state_fetched
        if not tool_calls:
            tool_calls = result.get("tool_results") or []

    # Prefer the verifier's structured decision over the heuristic.
    if state_decision:
        decision = state_decision
    else:
        decision = _derive_decision(case, error, tool_calls, response_text)
    cost_usd = _estimate_cost(settings, prompt_tokens, completion_tokens)

    # Run evaluators
    cite_resolution = evaluators.citation_resolution(citations, fetched_refs)
    cite_completeness = evaluators.citation_completeness(case, citations)
    facts = evaluators.required_facts(case, response_text)
    forbidden = evaluators.forbidden_claims(case, response_text)
    leaks = evaluators.pid_leak(case, response_text, citations)
    decision_score = evaluators.decision_match(case, decision)
    latency_score = evaluators.latency_check(case, latency_ms)
    cost_score = evaluators.cost_check(case, cost_usd)
    adversarial = evaluators.adversarial_defense(case, response_text, citations)

    workflow_match = {
        "matched": (
            case.expected_workflow is None
            or state_workflow == case.expected_workflow
        ),
        "got": state_workflow,
        "expected": case.expected_workflow,
        "confidence": state_confidence,
    }

    scores = {
        "citation_resolution": cite_resolution,
        "citation_completeness": cite_completeness,
        "required_facts": facts,
        "forbidden_claims": forbidden,
        "pid_leak": leaks,
        "decision": decision_score,
        "workflow": workflow_match,
        "latency": latency_score,
        "cost": cost_score,
        "adversarial": adversarial,
    }

    failures: list[str] = []
    if error is not None:
        failures.append(f"runtime error: {error}")
    if not decision_score["matched"]:
        failures.append(
            f"decision mismatch: expected {case.expected_decision!r}, got {decision!r}"
        )
    # NOTE: workflow_match is recorded for trend analysis but not enforced as
    # a gate yet. Until the graph branches by workflow_id (UC-1 triage flow
    # lands separately), W-2/W-7 etc. all route to the same agent and the
    # label distinction is informational. Re-enable as a hard failure when
    # routing actually branches per workflow.
    if cite_resolution["unresolved"]:
        failures.append(
            f"unresolved citations: {cite_resolution['unresolved']}"
        )
    if cite_completeness["score"] < case.citation_completeness_min:
        failures.append(
            f"citation completeness {cite_completeness['score']:.2f} < "
            f"required {case.citation_completeness_min:.2f}; missing={cite_completeness['missing']}"
        )
    if facts["missing"]:
        failures.append(f"missing required facts: {facts['missing']}")
    if forbidden["count"] > 0:
        failures.append(f"forbidden claims appeared: {forbidden['violations']}")
    if leaks["count"] > 0:
        failures.append(f"PID leak detected (release blocker): {leaks['leaks']}")
    if not latency_score["within_budget"]:
        failures.append(
            f"latency {latency_ms}ms exceeded {case.latency_ms_max}ms"
        )
    if not cost_score["within_budget"]:
        failures.append(
            f"cost ${cost_usd:.4f} exceeded ${case.cost_usd_max:.4f}"
        )

    passed = error is None and not failures
    case_result = CaseResult(
        case=case,
        passed=passed,
        response_text=response_text,
        citations=citations,
        tool_calls=tool_calls,
        decision=decision,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        scores=scores,
        failures=failures,
        error=error,
    )

    if langfuse is not None and langfuse.enabled:
        case_result.trace_id = langfuse.record_case(case_result)

    return case_result


def _extract_observed(
    result: dict[str, Any],
) -> tuple[str, list[str], list[dict[str, Any]], set[str], int, int]:
    """Pull response text, citations, tool calls, and fetched refs from the
    react-agent result. Token counts are best-effort from message metadata."""
    response_text = ""
    tool_calls: list[dict[str, Any]] = []
    fetched_refs: set[str] = set()
    prompt_tokens = 0
    completion_tokens = 0

    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in messages:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        {
                            "name": tc.get("name"),
                            "args": tc.get("args") or {},
                            "id": tc.get("id"),
                        }
                    )
            else:
                # Final assistant message
                response_text = msg.content if isinstance(msg.content, str) else str(msg.content)
            usage = getattr(msg, "usage_metadata", None) or {}
            prompt_tokens += int(usage.get("input_tokens") or 0)
            completion_tokens += int(usage.get("output_tokens") or 0)
        elif isinstance(msg, ToolMessage):
            # Harvest fhir_refs from the tool result (so citation_resolution
            # has a real "fetched in this turn" set).
            fetched_refs.update(_extract_refs_from_tool_message(msg))

    citations = evaluators.extract_citations(response_text)
    return response_text, citations, tool_calls, fetched_refs, prompt_tokens, completion_tokens


def _extract_refs_from_tool_message(msg: ToolMessage) -> set[str]:
    """Best-effort: scan the tool message content for ``fhir_ref`` values."""
    content = msg.content
    if not isinstance(content, str):
        return set()
    # The tool wrappers serialize rows as JSON; ``fhir_ref`` appears as a string field.
    # A regex avoids paying for a JSON parse on every tool message.
    import re as _re

    pattern = _re.compile(r'"fhir_ref"\s*:\s*"([^"]+)"')
    return set(pattern.findall(content))


def _derive_decision(
    case: Case,
    error: str | None,
    tool_calls: list[dict[str, Any]],
    response_text: str,
) -> str:
    """Map runtime observations to a decision label.

    The full taxonomy from ARCHITECTURE.md §10 (allow / refused_unsourced /
    refused_safety / tool_failure / denied_authz / breakglass / blocked_baa)
    requires audit-row introspection that lands with the verifier node. For
    now we infer from observable signals; the verifier will replace this.
    """
    if error is not None:
        return "tool_failure"
    text_lower = (response_text or "").lower()
    # Match the literal authz-refusal phrasing from ARCHITECTURE.md Appendix B.
    # Bare "denied" is too broad — patients commonly "deny chest pain" etc.
    if "you don't have access" in text_lower or "you do not have access" in text_lower:
        return "denied_authz"
    if "break-glass" in text_lower and "active" in text_lower:
        return "breakglass"
    if "couldn't ground" in text_lower or "cannot ground" in text_lower:
        return "refused_unsourced"
    if "ai temporarily unavailable" in text_lower:
        return "tool_failure"
    return "allow"


def _estimate_cost(settings: Settings, prompt_tokens: int, completion_tokens: int) -> float:
    """Rough USD estimate based on per-million token rates.

    Defaults are conservative public list prices; real measurement will come
    via Langfuse usage tracking once the callback handler is wired in.
    """
    provider = settings.llm_provider.lower()
    rates_per_million_in: dict[str, float] = {
        "openai": 0.15,  # gpt-4o-mini default
        "anthropic": 3.00,  # sonnet 4.6 input
    }
    rates_per_million_out: dict[str, float] = {
        "openai": 0.60,
        "anthropic": 15.00,
    }
    in_rate = rates_per_million_in.get(provider, 0.15)
    out_rate = rates_per_million_out.get(provider, 0.60)
    return (prompt_tokens * in_rate + completion_tokens * out_rate) / 1_000_000
