"""Eval runner — execute one case end-to-end and score it.

The runner is a thin orchestrator:
1. Build the agent graph for the case's persona/patient context.
2. Invoke the graph with the case's input message.
3. Extract response text, citations, tool calls, latency, and cost.
4. Run all evaluators against the result.
5. Aggregate into a ``CaseResult`` and (optionally) push to Langfuse.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ..config import Settings, get_settings
from ..graph import build_graph
from ..observability import get_callback_handler
from . import evaluators
from .case import Case, CaseResult, DimensionResult
from .faithfulness import FaithfulnessJudge, build_default_haiku_factory
from .langfuse_client import LangfuseClient

_log = logging.getLogger(__name__)


def _maybe_default_judge(settings: Settings) -> FaithfulnessJudge | None:
    """Build the default Haiku judge if the env supports it, else ``None``.

    Skips silently when ``EVAL_JUDGE_MODEL`` is unset or ``ANTHROPIC_API_KEY``
    is missing — runs in CI / fixture environments without an Anthropic
    account simply don't get the faithfulness dimension. Tests inject their
    own ``faithfulness_judge`` to bypass this path.
    """
    model_name = (settings.eval_judge_model or "").strip()
    if not model_name:
        return None
    api_key = settings.anthropic_api_key.get_secret_value()
    if not api_key:
        return None
    factory: Callable[[], Any] = build_default_haiku_factory(api_key, model_name=model_name)
    return FaithfulnessJudge(llm_factory=factory, model_name=model_name)


async def run_case(
    case: Case,
    settings: Settings | None = None,
    langfuse: LangfuseClient | None = None,
    *,
    faithfulness_judge: FaithfulnessJudge | None = None,
) -> CaseResult:
    """Execute a single case and return its scored result.

    ``faithfulness_judge`` is optional: when ``None``, the runner builds the
    default Haiku-backed judge if ``ANTHROPIC_API_KEY`` is available, and
    skips faithfulness scoring otherwise. Tests pass an injected judge with
    a stub LLM so they don't spend tokens.
    """
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
    fetched_resources: dict[str, dict[str, Any]] = {}
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
        (
            response_text,
            citations,
            tool_calls,
            fetched_refs,
            fetched_resources,
            prompt_tokens,
            completion_tokens,
        ) = _extract_observed(result)
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

    # Score each gate as a DimensionResult. Overall pass = no error AND every
    # dimension passes (CaseResult.recompute_passed below). The ``failures``
    # list keeps a flat human-readable view for pytest output; it mirrors the
    # dimensions exactly, so the test failure message stays identical to the
    # pre-refactor shape.
    dimensions: dict[str, DimensionResult] = {}
    failures: list[str] = []

    if error is not None:
        failures.append(f"runtime error: {error}")

    decision_passed = bool(decision_score["matched"])
    dimensions["decision"] = DimensionResult(
        name="decision",
        passed=decision_passed,
        score=1.0 if decision_passed else 0.0,
        details=decision_score,
    )
    if not decision_passed:
        failures.append(
            f"decision mismatch: expected {case.expected_decision!r}, got {decision!r}"
        )

    # NOTE: workflow_match is recorded for trend analysis but not enforced as
    # a gate yet. Until the graph branches by workflow_id (UC-1 triage flow
    # lands separately), W-2/W-7 etc. all route to the same agent and the
    # label distinction is informational. Re-enable as a hard failure when
    # routing actually branches per workflow.

    cite_resolution_passed = not cite_resolution["unresolved"]
    dimensions["citation_resolution"] = DimensionResult(
        name="citation_resolution",
        passed=cite_resolution_passed,
        score=float(cite_resolution["score"]),
        details=cite_resolution,
    )
    if not cite_resolution_passed:
        failures.append(
            f"unresolved citations: {cite_resolution['unresolved']}"
        )

    citation_passed = cite_completeness["score"] >= case.citation_completeness_min
    dimensions["citation"] = DimensionResult(
        name="citation",
        passed=citation_passed,
        score=float(cite_completeness["score"]),
        details=cite_completeness,
    )
    if not citation_passed:
        failures.append(
            f"citation completeness {cite_completeness['score']:.2f} < "
            f"required {case.citation_completeness_min:.2f}; missing={cite_completeness['missing']}"
        )

    substring_passed = not facts["missing"]
    dimensions["substring"] = DimensionResult(
        name="substring",
        passed=substring_passed,
        score=float(facts["score"]),
        details=facts,
    )
    if not substring_passed:
        failures.append(f"missing required facts: {facts['missing']}")

    forbidden_passed = forbidden["count"] == 0
    dimensions["forbidden"] = DimensionResult(
        name="forbidden",
        passed=forbidden_passed,
        score=1.0 if forbidden_passed else 0.0,
        details=forbidden,
    )
    if not forbidden_passed:
        failures.append(f"forbidden claims appeared: {forbidden['violations']}")

    pid_leak_passed = leaks["count"] == 0
    dimensions["pid_leak"] = DimensionResult(
        name="pid_leak",
        passed=pid_leak_passed,
        score=1.0 if pid_leak_passed else 0.0,
        details=leaks,
    )
    if not pid_leak_passed:
        failures.append(f"PID leak detected (release blocker): {leaks['leaks']}")

    latency_passed = bool(latency_score["within_budget"])
    dimensions["latency"] = DimensionResult(
        name="latency",
        passed=latency_passed,
        score=float(latency_ms),
        details=latency_score,
    )
    if not latency_passed:
        failures.append(
            f"latency {latency_ms}ms exceeded {case.latency_ms_max}ms"
        )

    cost_passed = bool(cost_score["within_budget"])
    dimensions["cost"] = DimensionResult(
        name="cost",
        passed=cost_passed,
        score=float(cost_usd),
        details=cost_score,
    )
    if not cost_passed:
        failures.append(
            f"cost ${cost_usd:.4f} exceeded ${case.cost_usd_max:.4f}"
        )

    # Faithfulness (issues 011 + 012). Skip when the agent erred — there's
    # no response text to judge — and when the judge can't be constructed
    # (no ANTHROPIC_API_KEY in env). Skipped cases simply don't get a
    # faithfulness DimensionResult, so the scoreboard column reports the
    # rate over only cases that scored it.
    judge = faithfulness_judge or _maybe_default_judge(settings)
    if error is None and judge is not None:
        try:
            faith_result = await judge.judge(
                response_text,
                fetched_resources,
                langfuse=langfuse,
            )
            faith_dim = faith_result.to_dimension_result()
            dimensions["faithfulness"] = faith_dim
            scores["faithfulness"] = faith_dim.details
            if not faith_dim.passed:
                # Surface citation-grounding failures and uncited-claim
                # failures separately so pytest output makes the failure
                # mode obvious without opening Langfuse.
                failure_parts: list[str] = []
                unsupported = faith_dim.details.get("unsupported", []) or []
                if unsupported:
                    rendered = "; ".join(
                        f"{u['ref']}: {u['reasoning']}" for u in unsupported[:3]
                    )
                    failure_parts.append(
                        f"{faith_dim.details.get('supported_count')}/"
                        f"{faith_dim.details.get('total_citations')} citations supported; "
                        f"first unsupported: {rendered}"
                    )
                uncited = faith_dim.details.get("uncited_claims", []) or []
                if uncited:
                    rendered_uncited = "; ".join(
                        f"'{c}'" for c in uncited[:3]
                    )
                    failure_parts.append(
                        f"{len(uncited)} uncited clinical claim(s) flagged: "
                        f"{rendered_uncited}"
                    )
                if not failure_parts:
                    # Defensive: pass=False with neither failure mode means a
                    # bookkeeping bug in the judge; surface it rather than
                    # swallowing.
                    failure_parts.append("faithfulness verdict failed with no detail")
                failures.append("faithfulness failed: " + " | ".join(failure_parts))
        except Exception as exc:
            # Judge failure is informational — don't bring down the eval run.
            _log.warning("faithfulness judge failed for case %s: %s", case.id, exc)

    case_result = CaseResult(
        case=case,
        passed=False,  # set by recompute_passed below
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
        dimensions=dimensions,
        error=error,
    )
    case_result.recompute_passed()

    if langfuse is not None and langfuse.enabled:
        case_result.trace_id = langfuse.record_case(case_result)

    return case_result


def _extract_observed(
    result: dict[str, Any],
) -> tuple[
    str,
    list[str],
    list[dict[str, Any]],
    set[str],
    dict[str, dict[str, Any]],
    int,
    int,
]:
    """Pull response text, citations, tool calls, fetched refs, and per-ref
    resource bodies from the react-agent result.

    The per-ref bodies feed the FaithfulnessJudge: each ``<cite ref="X"/>``
    in the response is judged against the body keyed by ``X``. Token counts
    are best-effort from message metadata.
    """
    response_text = ""
    tool_calls: list[dict[str, Any]] = []
    fetched_refs: set[str] = set()
    fetched_resources: dict[str, dict[str, Any]] = {}
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
            # Harvest fhir_refs and the row body keyed by ref so the
            # FaithfulnessJudge can fetch the cited resource without
            # re-running the tool.
            for ref, body in _extract_resources_from_tool_message(msg).items():
                fetched_refs.add(ref)
                # Last-write-wins on duplicate refs across tool calls; the
                # repeat is almost always the same row anyway.
                fetched_resources[ref] = body

    citations = evaluators.extract_citations(response_text)
    return (
        response_text,
        citations,
        tool_calls,
        fetched_refs,
        fetched_resources,
        prompt_tokens,
        completion_tokens,
    )


_FHIR_REF_PATTERN = re.compile(r'"fhir_ref"\s*:\s*"([^"]+)"')


def _extract_resources_from_tool_message(msg: ToolMessage) -> dict[str, dict[str, Any]]:
    """Best-effort: parse the tool message JSON and key each row by fhir_ref.

    Falls back to a regex-only ref scan (with empty bodies) when the
    content isn't parseable JSON, so older / non-standard tool outputs
    still contribute to the ``fetched_refs`` set.
    """
    content = msg.content
    if not isinstance(content, str) or not content:
        return {}

    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return {ref: {} for ref in _FHIR_REF_PATTERN.findall(content)}

    return {ref: body for ref, body in _walk_for_rows(payload)}


def _walk_for_rows(node: Any):
    """Yield ``(fhir_ref, row_body)`` pairs from any nested dict / list.

    Tool results are usually ``{"ok": True, "rows": [{...}]}`` but composite
    tools may nest their results deeper; walk recursively so nothing is
    missed.
    """
    if isinstance(node, dict):
        ref = node.get("fhir_ref")
        if isinstance(ref, str) and ref:
            # Pass a shallow projection (drop the ref itself so it doesn't
            # echo back into the judge prompt as noise).
            body = {k: v for k, v in node.items() if k != "fhir_ref"}
            yield ref, body
        for v in node.values():
            yield from _walk_for_rows(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_for_rows(item)


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
