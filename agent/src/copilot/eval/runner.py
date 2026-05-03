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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ..checkpointer import build_memory_checkpointer
from ..config import Settings, get_settings
from ..graph import build_graph
from ..observability import get_callback_handler
from . import evaluators
from .case import Case, CaseResult, DimensionResult, Turn
from .faithfulness import FaithfulnessJudge, build_default_haiku_factory
from .langfuse_client import LangfuseClient
from .trajectory import evaluate_trajectory

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
    graph_factory: Callable[..., Any] | None = None,
) -> CaseResult:
    """Execute a single case and return its scored result.

    ``faithfulness_judge`` is optional: when ``None``, the runner builds the
    default Haiku-backed judge if ``ANTHROPIC_API_KEY`` is available, and
    skips faithfulness scoring otherwise. Tests pass an injected judge with
    a stub LLM so they don't spend tokens.

    ``graph_factory`` is optional: when ``None``, ``build_graph`` is used.
    Tests inject a stub graph here so they don't need an LLM. Multi-turn
    cases pass ``checkpointer=`` to the factory so all turns share one
    in-memory ``MemorySaver``.
    """
    settings = settings or get_settings()
    factory: Callable[..., Any] = graph_factory or build_graph

    # Multi-turn cases (issue 015) get their own dispatch — fresh
    # ``MemorySaver`` per case, one shared ``thread_id`` across turns, every
    # applicable dimension scored on every turn, ``multi_turn`` rollup
    # dimension at the case level.
    if len(case.turns) > 1:
        return await _run_multi_turn(
            case,
            settings=settings,
            langfuse=langfuse,
            faithfulness_judge=faithfulness_judge,
            graph_factory=factory,
        )

    if len(case.turns) == 0:
        raise ValueError(f"case {case.id}: empty turns list — invalid")

    # Build the full graph (agent → verifier) so eval runs exercise the
    # verifier's citation-resolution check, not just the bare agent.
    graph = factory(settings)
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

    # Trajectory (issue 013) — set-membership over the agent's tool calls.
    # Cases with empty ``required_tools`` always pass this dimension so it
    # attaches to every CaseResult and the scoreboard sees a column whose
    # rate is over only cases that opted in.
    trajectory_result = evaluate_trajectory(tool_calls, case.required_tools)
    dimensions["trajectory"] = trajectory_result.to_dimension_result()
    scores["trajectory"] = dimensions["trajectory"].details
    if not trajectory_result.passed:
        failures.append(
            f"trajectory missing required tool(s): {trajectory_result.missing}"
        )

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


# ---------------------------------------------------------------------------
# Multi-turn (issue 015)
# ---------------------------------------------------------------------------


async def _run_multi_turn(
    case: Case,
    *,
    settings: Settings,
    langfuse: LangfuseClient | None,
    faithfulness_judge: FaithfulnessJudge | None,
    graph_factory: Callable[..., Any],
) -> CaseResult:
    """Run a multi-turn case end-to-end.

    Builds one graph compiled with a fresh ``MemorySaver`` checkpointer and
    invokes it once per turn against a single, case-unique ``thread_id`` so
    LangGraph threads conversation history across turns. Per-turn dimensions
    (substring / citation / citation_resolution / trajectory / faithfulness /
    forbidden / pid_leak / decision) are scored on each turn; case-level
    dimensions are the AND across turns. ``multi_turn`` summarizes the
    fraction of turns that passed every applicable dimension, attached as
    its own ``DimensionResult`` and as standardized score
    ``multi_turn.turn_pass_rate`` for langfuse rollups.

    Latency and cost are summed across turns and checked against the
    case-level budget.
    """
    # Build graph with a fresh MemorySaver so this case's thread doesn't
    # collide with concurrent eval runs on the same process.
    checkpointer = build_memory_checkpointer()
    graph = graph_factory(settings, checkpointer=checkpointer)
    thread_id = f"eval-mt-{case.id}-{uuid.uuid4().hex[:8]}"

    patient_id = case.patient_id if case.patient_id is not None else "fixture-1"

    judge = faithfulness_judge or _maybe_default_judge(settings)

    per_turn: list[_TurnRecord] = []

    for turn_index, turn in enumerate(case.turns):
        record = await _invoke_and_score_turn(
            graph,
            case=case,
            turn=turn,
            turn_index=turn_index,
            patient_id=patient_id,
            thread_id=thread_id,
            settings=settings,
            langfuse=langfuse,
            faithfulness_judge=judge,
        )
        per_turn.append(record)

    case_result = _aggregate_multi_turn(case, per_turn)

    if langfuse is not None and langfuse.enabled:
        case_result.trace_id = langfuse.record_case(case_result)

    return case_result


@dataclass
class _TurnRecord:
    """Per-turn observation + dimension results for the multi-turn aggregator."""

    turn_index: int
    turn: Turn
    response_text: str
    citations: list[str]
    tool_calls: list[dict[str, Any]]
    decision: str
    latency_ms: int
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    error: str | None
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    scores: dict[str, Any] = field(default_factory=dict)

    @property
    def turn_passed(self) -> bool:
        """A turn passes when every per-turn dimension passed and there is
        no runtime error."""
        if self.error is not None:
            return False
        return all(d.passed for d in self.dimensions.values())


async def _invoke_and_score_turn(
    graph: Any,
    *,
    case: Case,
    turn: Turn,
    turn_index: int,
    patient_id: str,
    thread_id: str,
    settings: Settings,
    langfuse: LangfuseClient | None,
    faithfulness_judge: FaithfulnessJudge | None,
) -> _TurnRecord:
    """Invoke the graph for one turn and score every applicable dimension.

    Each invocation sends only the new ``HumanMessage`` — the LangGraph
    checkpointer attached to the compiled graph re-hydrates prior turns from
    ``thread_id``. The runner does not replay history itself; doing so would
    double-count messages.
    """
    invoke_config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    handler = get_callback_handler(settings)
    if handler is not None:
        invoke_config["callbacks"] = [handler]

    error: str | None = None
    response_text = ""
    citations: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    fetched_refs: set[str] = set()
    fetched_resources: dict[str, dict[str, Any]] = {}
    prompt_tokens = 0
    completion_tokens = 0
    state_decision: str | None = None
    state_fetched: set[str] = set()

    start = time.monotonic()
    try:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=turn.prompt)],
                "patient_id": patient_id,
                "conversation_id": thread_id,
                "user_id": case.user_id or "eval",
            },
            config=invoke_config,
        )
        state_decision = result.get("decision")
        state_fetched = set(result.get("fetched_refs") or [])
    except Exception as exc:
        # Eval should never bring down the runner — surface the error as a
        # turn-level failure and let the aggregator mark the case failed.
        error = f"{type(exc).__name__}: {exc}"
        _log.exception("case %s turn %d raised", case.id, turn_index)
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
        fetched_refs = fetched_refs | state_fetched
        if not tool_calls:
            tool_calls = result.get("tool_results") or []

    decision = state_decision or _derive_decision(case, error, tool_calls, response_text)
    cost_usd = _estimate_cost(settings, prompt_tokens, completion_tokens)

    record = _TurnRecord(
        turn_index=turn_index,
        turn=turn,
        response_text=response_text,
        citations=citations,
        tool_calls=tool_calls,
        decision=decision,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error=error,
    )

    label = f"turn {turn_index + 1}"

    if error is not None:
        record.failures.append(f"[{label}] runtime error: {error}")

    # Per-turn dimensions read the Turn fields directly (not Case projections,
    # which always point at turns[0]).
    _score_substring_for_turn(record, label)
    _score_citation_resolution_for_turn(record, fetched_refs, label)
    _score_citation_completeness_for_turn(record, label)
    _score_trajectory_for_turn(record, label)
    _score_decision_for_turn(record, case, label)
    _score_forbidden_for_turn(record, case, label)
    _score_pid_leak_for_turn(record, case, label)

    if error is None and faithfulness_judge is not None:
        try:
            faith_result = await faithfulness_judge.judge(
                response_text,
                fetched_resources,
                langfuse=langfuse,
            )
            faith_dim = faith_result.to_dimension_result()
            record.dimensions["faithfulness"] = faith_dim
            record.scores["faithfulness"] = faith_dim.details
            if not faith_dim.passed:
                record.failures.append(
                    f"[{label}] faithfulness failed: "
                    f"{faith_dim.details.get('supported_count')}/"
                    f"{faith_dim.details.get('total_citations')} citations supported, "
                    f"{len(faith_dim.details.get('uncited_claims') or [])} uncited"
                )
        except Exception as exc:
            # Judge failure is informational — match the single-turn path's
            # fail-open behavior so a flaky judge doesn't fail eval cases.
            _log.warning(
                "faithfulness judge failed for case %s turn %d: %s",
                case.id,
                turn_index,
                exc,
            )

    return record


def _score_substring_for_turn(record: _TurnRecord, label: str) -> None:
    turn = record.turn
    if not turn.must_contain:
        details = {"score": 1.0, "missing": [], "total": 0}
    else:
        text = (record.response_text or "").lower()
        missing = [f for f in turn.must_contain if f.lower() not in text]
        score = (len(turn.must_contain) - len(missing)) / len(turn.must_contain)
        details = {"score": score, "missing": missing, "total": len(turn.must_contain)}
    passed = not details["missing"]
    record.dimensions["substring"] = DimensionResult(
        name="substring",
        passed=passed,
        score=float(details["score"]),
        details=details,
    )
    record.scores["required_facts"] = details
    if not passed:
        record.failures.append(f"[{label}] missing required facts: {details['missing']}")


def _score_citation_resolution_for_turn(
    record: _TurnRecord, fetched_refs: set[str], label: str
) -> None:
    details = evaluators.citation_resolution(record.citations, fetched_refs)
    passed = not details["unresolved"]
    record.dimensions["citation_resolution"] = DimensionResult(
        name="citation_resolution",
        passed=passed,
        score=float(details["score"]),
        details=details,
    )
    record.scores["citation_resolution"] = details
    if not passed:
        record.failures.append(
            f"[{label}] unresolved citations: {details['unresolved']}"
        )


def _score_citation_completeness_for_turn(record: _TurnRecord, label: str) -> None:
    turn = record.turn
    required = turn.must_cite
    if not required:
        details = {"score": 1.0, "missing": [], "total": 0}
    else:
        cite_set = set(record.citations)
        missing = [r for r in required if r not in cite_set]
        score = (len(required) - len(missing)) / len(required)
        details = {"score": score, "missing": missing, "total": len(required)}
    # Per-turn citation completeness must be 100% on the refs the turn
    # declared — the case-wide ``citation_completeness_min`` threshold
    # exists for the single-turn shape and is not relaxed across turns.
    passed = not details["missing"]
    record.dimensions["citation"] = DimensionResult(
        name="citation",
        passed=passed,
        score=float(details["score"]),
        details=details,
    )
    record.scores["citation_completeness"] = details
    if not passed:
        record.failures.append(
            f"[{label}] citation completeness {details['score']:.2f} < 1.00; "
            f"missing={details['missing']}"
        )


def _score_trajectory_for_turn(record: _TurnRecord, label: str) -> None:
    traj = evaluate_trajectory(record.tool_calls, record.turn.required_tools)
    record.dimensions["trajectory"] = traj.to_dimension_result()
    record.scores["trajectory"] = record.dimensions["trajectory"].details
    if not traj.passed:
        record.failures.append(
            f"[{label}] trajectory missing required tool(s): {traj.missing}"
        )


def _score_decision_for_turn(record: _TurnRecord, case: Case, label: str) -> None:
    matched = record.decision == case.expected_decision
    details = {
        "matched": matched,
        "got": record.decision,
        "expected": case.expected_decision,
    }
    record.dimensions["decision"] = DimensionResult(
        name="decision",
        passed=matched,
        score=1.0 if matched else 0.0,
        details=details,
    )
    record.scores["decision"] = details
    if not matched:
        record.failures.append(
            f"[{label}] decision mismatch: expected {case.expected_decision!r}, "
            f"got {record.decision!r}"
        )


def _score_forbidden_for_turn(record: _TurnRecord, case: Case, label: str) -> None:
    text = (record.response_text or "").lower()
    violations = [c for c in case.forbidden_claims if c.lower() in text]
    details = {"violations": violations, "count": len(violations)}
    passed = not violations
    record.dimensions["forbidden"] = DimensionResult(
        name="forbidden",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=details,
    )
    record.scores["forbidden_claims"] = details
    if not passed:
        record.failures.append(f"[{label}] forbidden claims appeared: {violations}")


def _score_pid_leak_for_turn(record: _TurnRecord, case: Case, label: str) -> None:
    details = evaluators.pid_leak(case, record.response_text, record.citations)
    passed = details["count"] == 0
    record.dimensions["pid_leak"] = DimensionResult(
        name="pid_leak",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=details,
    )
    record.scores["pid_leak"] = details
    if not passed:
        record.failures.append(
            f"[{label}] PID leak detected (release blocker): {details['leaks']}"
        )


def _aggregate_multi_turn(case: Case, per_turn: list[_TurnRecord]) -> CaseResult:
    """Roll per-turn ``_TurnRecord``s up into a single ``CaseResult``.

    For each named dimension, the case-level dim is the AND across turns
    (a turn that didn't score the dim doesn't drag it down). The
    ``multi_turn`` dim summarizes the fraction of turns that passed every
    applicable dimension. Latency and cost are summed and checked against
    the case-level budget at the end.
    """
    # Union of dimension names that any turn scored.
    dim_names: list[str] = []
    seen: set[str] = set()
    for record in per_turn:
        for name in record.dimensions:
            if name not in seen:
                seen.add(name)
                dim_names.append(name)

    aggregated: dict[str, DimensionResult] = {}
    for name in dim_names:
        scoring_turns = [r for r in per_turn if name in r.dimensions]
        all_passed = all(r.dimensions[name].passed for r in scoring_turns)
        # Mean score across turns that scored the dim — gives the scoreboard
        # a continuous read on how a dim is trending across multi-turn cases.
        scores_seen = [
            r.dimensions[name].score
            for r in scoring_turns
            if r.dimensions[name].score is not None
        ]
        mean_score = (
            sum(scores_seen) / len(scores_seen) if scores_seen else None
        )
        aggregated[name] = DimensionResult(
            name=name,
            passed=all_passed,
            score=mean_score,
            details={
                "per_turn": [
                    {
                        "turn_index": r.turn_index,
                        "passed": r.dimensions[name].passed,
                        "score": r.dimensions[name].score,
                        "details": r.dimensions[name].details,
                    }
                    for r in scoring_turns
                ],
            },
        )

    turns_passed = sum(1 for r in per_turn if r.turn_passed)
    turn_pass_rate = turns_passed / len(per_turn) if per_turn else 0.0
    aggregated["multi_turn"] = DimensionResult(
        name="multi_turn",
        passed=turn_pass_rate >= 1.0,
        score=turn_pass_rate,
        details={
            "turn_pass_rate": turn_pass_rate,
            "turns_total": len(per_turn),
            "turns_passed": turns_passed,
            "per_turn_passed": [r.turn_passed for r in per_turn],
        },
    )

    # Case-wide latency/cost budgets — sum across turns then check.
    total_latency = sum(r.latency_ms for r in per_turn)
    total_cost = sum(r.cost_usd for r in per_turn)
    latency_passed = (
        case.latency_ms_max is None or total_latency <= case.latency_ms_max
    )
    aggregated["latency"] = DimensionResult(
        name="latency",
        passed=latency_passed,
        score=float(total_latency),
        details={
            "within_budget": latency_passed,
            "got_ms": total_latency,
            "limit_ms": case.latency_ms_max,
        },
    )
    cost_passed = case.cost_usd_max is None or total_cost <= case.cost_usd_max
    aggregated["cost"] = DimensionResult(
        name="cost",
        passed=cost_passed,
        score=float(total_cost),
        details={
            "within_budget": cost_passed,
            "got_usd": total_cost,
            "limit_usd": case.cost_usd_max,
        },
    )

    aggregated_failures: list[str] = []
    aggregated_errors: list[str] = []
    for record in per_turn:
        aggregated_failures.extend(record.failures)
        if record.error is not None:
            aggregated_errors.append(f"turn {record.turn_index + 1}: {record.error}")
    if not latency_passed:
        aggregated_failures.append(
            f"latency {total_latency}ms exceeded {case.latency_ms_max}ms (sum across turns)"
        )
    if not cost_passed:
        aggregated_failures.append(
            f"cost ${total_cost:.4f} exceeded ${case.cost_usd_max:.4f} (sum across turns)"
        )

    aggregated_tool_calls: list[dict[str, Any]] = []
    aggregated_citations: list[str] = []
    for record in per_turn:
        aggregated_tool_calls.extend(record.tool_calls)
        for c in record.citations:
            if c not in aggregated_citations:
                aggregated_citations.append(c)

    response_blocks = [
        f"[turn {r.turn_index + 1}] {r.response_text}".rstrip()
        for r in per_turn
    ]
    response_text = "\n\n".join(response_blocks)

    case_result = CaseResult(
        case=case,
        passed=False,  # set by recompute_passed below
        response_text=response_text,
        citations=aggregated_citations,
        tool_calls=aggregated_tool_calls,
        decision=per_turn[-1].decision if per_turn else "tool_failure",
        latency_ms=total_latency,
        cost_usd=total_cost,
        prompt_tokens=sum(r.prompt_tokens for r in per_turn),
        completion_tokens=sum(r.completion_tokens for r in per_turn),
        scores={
            "multi_turn": aggregated["multi_turn"].details,
        },
        failures=aggregated_failures,
        dimensions=aggregated,
        error="; ".join(aggregated_errors) if aggregated_errors else None,
    )
    case_result.recompute_passed()
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
