"""Co-Pilot StateGraph.

Issue 003 collapsed the EHR-launch-era ``triage_node`` / ``agent_node`` split
into a single tool-calling node and demoted the classifier to an advisory
hint. The graph topology is now ``classifier → (clarify | agent) → verifier
→ END`` with verifier-driven regen looping back to ``agent``.

The classifier still emits ``{ workflow_id, confidence }`` and the runtime
records both in the audit row, but the values do not gate the tool surface:
all tools are bound to one node and the LLM picks. Workflow-specific
behavior comes from the synthesis prompts (issues 006/007) and from the
LLM choosing composite vs granular tools.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

from .audit import AuditEvent, now_iso, write_audit_event
from .blocks import (
    block_from_clarify_text,
    build_citations,
    extract_cite_attributes,
    plain_block_from_text,
    refusal_plain_block,
    synthesize_overnight_block,
)
from .care_team import AuthDecision
from .checkpointer import build_memory_checkpointer
from .config import Settings, get_settings
from .cost_tracking import (
    CallCost,
    aggregate_turn_cost,
    estimate_call_cost,
)
from .llm import build_chat_model
from .prompts import CLARIFY_SYSTEM, CLASSIFIER_SYSTEM, build_system_prompt
from .state import CoPilotState
from .supervisor.graph import build_supervisor_node, route_after_supervisor
from .supervisor.workers import (
    build_evidence_retriever_node,
    build_intake_extractor_node,
)
from .tools import (
    make_tools,
    set_active_registry,
    set_active_smart_token,
    set_active_user_id,
)

_log = logging.getLogger(__name__)

MAX_REGENS = 2
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.8

# Auth-class errors emitted by the tool layer. Used to map a ToolMessage's
# error payload to a per-call gate decision for the audit row.
_AUTH_DECISIONS: frozenset[str] = frozenset(d.value for d in AuthDecision)
_DENIED_DECISIONS: frozenset[str] = frozenset(
    d.value for d in AuthDecision if d is not AuthDecision.ALLOWED
)


class WorkflowDecision(BaseModel):
    """Structured output from the classifier node (advisory)."""

    workflow_id: str = Field(
        description='One of "W-1"..."W-11" or "unclear"',
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Classifier confidence in [0.0, 1.0]",
    )


_SUPERVISOR_WORKFLOWS: frozenset[str] = frozenset({"W-DOC", "W-EVD"})
_COLD_START_AGENT_WORKFLOWS: frozenset[str] = frozenset({"W-1", "W-10"})


def _route_after_classifier(
    *,
    workflow_id: str,
    confidence: float,
    patient_id: str | None,
    focus_pid: str | None,
) -> str:
    """Decide whether the post-classifier edge goes to ``agent``, ``clarify``,
    or the new ``supervisor`` (issue 009 W-DOC / W-EVD routes).

    The classifier sees only the latest user message — it does not know
    whether ``patient_id`` (from session_context, e.g. EHR-launch) or
    ``focus_pid`` (resolved earlier this conversation) is already bound in
    state. For single-patient questions like "What happened to this patient
    overnight?" the classifier reasonably emits ``unclear`` / low confidence,
    which used to route every such turn into ``clarify_node`` (issue 018).

    Routing rules:
    1. Document / evidence intents (W-DOC, W-EVD) ALWAYS go to the
       supervisor regardless of patient binding — the supervisor owns
       the document + retrieval workers.
    2. Whenever a patient is already bound, short-circuit to ``agent``.
    3. Otherwise, if the classifier is unclear or below threshold, fall
       back to ``clarify``.
    4. Otherwise, ``agent``.
    """
    if workflow_id in _SUPERVISOR_WORKFLOWS:
        return "supervisor"
    if (patient_id or "").strip() or (focus_pid or "").strip():
        return "agent"
    if workflow_id in _COLD_START_AGENT_WORKFLOWS:
        return "agent"
    if workflow_id == "unclear" or confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
        return "clarify"
    return "agent"


# Match ``<cite ref="X"/>`` and ``<cite ref="X" extra="..."/>``. Issue 009
# extends the citation form with extra attributes (``page``, ``field``,
# ``value``, ``source``, ``section``) for DocumentReference and guideline
# refs; the verifier must capture only the ``ref`` value regardless of
# trailing attributes, otherwise valid document/guideline citations fall
# through as ``unresolved`` and trip the verifier's regen loop.
_CITE_QUOTE_CLASS = r'"\u201c\u201d\u2018\u2019'

_CITE_PATTERN = re.compile(
    rf"<cite\s+ref\s*=\s*[{_CITE_QUOTE_CLASS}]"
    rf"([^{_CITE_QUOTE_CLASS}]+)[{_CITE_QUOTE_CLASS}][^>]*/?\s*>",
    flags=re.IGNORECASE,
)
_FHIR_REF_PATTERN = re.compile(r'"fhir_ref"\s*:\s*"([^"]+)"')
_FHIR_RESOURCE_REF_PATTERN = re.compile(
    r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-.]{1,64}$"
)
_TOOL_ERROR_PATTERN = re.compile(r'"error"\s*:\s*"([^"]+)"')
_TOOL_OK_PATTERN = re.compile(r'"ok"\s*:\s*(true|false)')
_TOOL_OK_FALSE_PATTERN = re.compile(r'"ok"\s*:\s*false', re.IGNORECASE)
_TOOL_STATUS_PATTERN = re.compile(r'"status"\s*:\s*"([^"]+)"')

# Issue 041 / 042: internal-leak markers that must never reach the
# clinician. The verifier scans the final AIMessage on W-EVD and W-1
# turns for these tokens and replaces the answer with a clean
# safe-failure copy when any are present. Worker names, probe names,
# raw tool error tokens, and HTTP-status fragments are debug surface —
# they belong in traces and the technical-details affordance, not in
# the clinical answer. Stored lowercase so the scan can fold-case the
# answer once.
_GUIDELINE_INTERNAL_LEAK_MARKERS: tuple[str, ...] = (
    "evidence_retriever",
    "intake_extractor",
    "retrieve_evidence",
    "run_panel_triage",
    "run_panel_med_safety",
    "list_panel",
    "careteam_denied",
    "denied_authz",
    "no_active_user",
    "no_active_patient",
    "retrieval_failed",
    "tool_failure",
    "http_404",
    "http_401",
    "http_403",
    "http_500",
    "http 401",
    "http 403",
    "http 404",
    "http 500",
)
# Issue 030: cache-hit signal emitted by the extraction tool's cache-served
# envelope (tools/extraction.py). Used by ``_cache_keys_from_tool_message``
# to surface a per-turn ``cache_hits`` field on the /chat response so the
# deployed e2e test can assert the second extraction was cache-served.
_TOOL_CACHE_HIT_PATTERN = re.compile(r'"cache_hit"\s*:\s*true', re.IGNORECASE)
_TOOL_CACHE_KEY_PATTERN = re.compile(r'"cache_key"\s*:\s*"([^"]+)"')


def _extract_citations(text: str) -> list[str]:
    seen: list[str] = []
    for match in _CITE_PATTERN.finditer(text or ""):
        ref = match.group(1).strip()
        if ref and ref not in seen:
            seen.append(ref)
    return seen


# Issue 028: heuristics for fail-closed verification of guideline / evidence
# answers. The W-EVD path runs the evidence_retriever worker, which fetches
# guideline chunks and synthesizes a clinical recommendation. If the
# synthesizer comes back asserting a clinical claim without a ratified
# guideline citation we must refuse rather than let uncited medical advice
# reach the user.
#
# These patterns intentionally mirror the eval rubric in
# ``copilot.eval.w2_evaluators`` so the verifier and offline grading agree
# on what counts as a clinical claim.
_CLINICAL_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numeric value with a clinical unit (vitals, labs, dose, target).
    re.compile(
        r"\b\d+(?:\.\d+)?\s*"
        r"(?:mg|mcg|mmol|mEq|mL|g|kg|bpm|mmHg|%|/dL|/L|/min)\b",
        re.IGNORECASE,
    ),
    # Lab name immediately followed by a numeric threshold.
    re.compile(
        r"\b(?:A1C|HbA1c|LDL|HDL|cholesterol|creatinine|potassium|sodium|"
        r"glucose|hemoglobin|WBC|platelets|BUN|GFR|TSH|INR)\b[^.?!]{0,40}\d",
        re.IGNORECASE,
    ),
    # Recommendation verb (recommends / should / target / first-line).
    re.compile(
        r"\b(?:recommend(?:s|ed|ation)?|should\s+(?:be|use|take|consider|"
        r"start|stop)|target(?:ing|ed)?|first[-\s]?line|prescrib(?:e|ed|"
        r"ing)|initiate(?:d|s)?|titrat(?:e|ed|ing))\b",
        re.IGNORECASE,
    ),
)

# Phrases that mark a clean evidence-gap admission. When the synthesizer
# explains "no relevant evidence", the absence of guideline citations is
# correct behavior, not a failure.
_EVIDENCE_GAP_PHRASES: tuple[str, ...] = (
    "no relevant guideline",
    "no relevant evidence",
    "could not find",
    "couldn't find",
    "couldn't ground",
    "cannot ground",
    "no citeable evidence",
    "no guideline evidence",
    "i don't see relevant evidence",
    "i can't find evidence",
    "no matching guideline",
    "without grounding",
    "evidence gap",
)


def _is_evidence_path(state: CoPilotState) -> bool:
    """True if this turn ran the evidence-retriever worker.

    The classifier emits ``W-EVD`` for guideline questions and the
    supervisor sets ``supervisor_action == retrieve_evidence`` when it
    dispatches the worker. Either signal is enough — they should agree
    on the happy path but the audit row is set from supervisor_action so
    we accept that as the authoritative trace.
    """
    if (state.get("workflow_id") or "") == "W-EVD":
        return True
    if (state.get("supervisor_action") or "") == "retrieve_evidence":
        return True
    return False


def _evidence_retrieval_failed(state: CoPilotState) -> bool:
    """True if a ``retrieve_evidence`` ToolMessage on this turn returned ``ok: false``.

    Issue 041: a failed retrieval call (connection error, no active user,
    empty query, etc.) means the corpus genuinely could not be consulted.
    Any clinical answer downstream of that — including the LLM's own
    paraphrase of the failure — must be replaced with a clean
    corpus-bound refusal so the clinician never sees a recommendation
    grounded in nothing.
    """
    for msg in state.get("messages", []) or []:
        if not isinstance(msg, ToolMessage):
            continue
        if (msg.name or "") != "retrieve_evidence":
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        if _TOOL_OK_FALSE_PATTERN.search(content):
            return True
    return False


def _has_internal_leak(text: str) -> bool:
    """True if ``text`` contains any marker that should never reach the clinician.

    Issue 041 acceptance: "Raw internal messages such as missing active
    user context, worker names, or HTTP statuses are hidden from the
    main answer." The verifier uses this to decide whether to rewrite
    the W-EVD path's final AIMessage.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _GUIDELINE_INTERNAL_LEAK_MARKERS)


def _is_document_path(state: CoPilotState) -> bool:
    """True if this turn ran the intake_extractor worker.

    Issue 035: the W-DOC turn's last AIMessage is what the verifier
    inspects, and a document-sourced clinical claim must always carry
    provenance. We accept either the classifier's ``W-DOC`` workflow id
    or the supervisor's ``extract`` action as the path signal.
    """
    if (state.get("workflow_id") or "") == "W-DOC":
        return True
    if (state.get("supervisor_action") or "") == "extract":
        return True
    return False


# Issue 042: panel-triage tools. The classifier's ``W-1`` is the primary
# signal but a turn can call ``run_panel_triage`` even on a misrouted
# workflow id, so the tool-name check is the defense-in-depth signal.
_PANEL_TRIAGE_TOOLS: frozenset[str] = frozenset(
    {"run_panel_triage", "run_panel_med_safety"}
)


def _is_panel_path(state: CoPilotState) -> bool:
    """True if this turn ran a panel-level tool or was classified as W-1.

    Issue 042: the agent_node uses this in combination with the
    ``sub_messages`` view (see ``_has_panel_tool_call`` /
    ``_has_failed_panel_tool_message``) to decide whether to enforce
    the panel-failure contract. ``state.messages`` only carries the
    final AIMessage (the LangGraph ``add_messages`` reducer does not
    accumulate the inner agent's ToolMessages), so the workflow_id is
    the only signal available here.
    """
    if (state.get("workflow_id") or "") == "W-1":
        return True
    for msg in state.get("messages", []) or []:
        if isinstance(msg, ToolMessage) and (msg.name or "") in _PANEL_TRIAGE_TOOLS:
            return True
    return False


def _panel_triage_failed(state: CoPilotState) -> bool:
    """True when a panel-level ToolMessage in ``state.messages`` is ``ok: false``.

    The runtime usually invokes the agent_node-side
    ``_has_failed_panel_tool_message(sub_messages)`` instead, since
    ``state.messages`` only carries the final AIMessage. This helper
    exists so external callers (and the predicate-level unit tests)
    can interrogate the same shape.
    """
    return _has_failed_panel_tool_message(state.get("messages", []) or [])


def _has_panel_tool_call(messages: list[Any]) -> bool:
    """True if ``messages`` contains a ToolMessage from a panel-level tool."""
    for msg in messages:
        if isinstance(msg, ToolMessage) and (msg.name or "") in _PANEL_TRIAGE_TOOLS:
            return True
    return False


def _has_failed_panel_tool_message(messages: list[Any]) -> bool:
    """True when any panel-level ToolMessage in ``messages`` is ``ok: false``.

    Issue 042: a failed panel call (FHIR transport error, HTTP 401/403
    via the gate, careteam_denied, etc.) means the panel data is
    genuinely unavailable. The agent_node uses this to decide whether
    to replace the answer with the panel-unavailable refusal copy.
    """
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if (msg.name or "") not in _PANEL_TRIAGE_TOOLS:
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        if _TOOL_OK_FALSE_PATTERN.search(content):
            return True
    return False


def _panel_triage_summary_from_tool_messages(messages: list[Any]) -> str | None:
    """Build a deterministic W-1 summary from a successful panel tool payload.

    The panel triage tool intentionally returns aggregate change-signal rows.
    Those rows are ranking hints, not FHIR resources, so asking the LLM to cite
    them tends to produce fabricated ``Observation/...`` refs. For W-1 we can
    summarize the successful tool output directly: the smoke/eval contract
    cares that the panel composite ran and that the CareTeam names surface.
    """
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if (msg.name or "") != "run_panel_triage":
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return None
        if payload.get("ok") is not True:
            return None
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None
        return _panel_triage_summary_from_rows(rows)
    return None


def _panel_triage_summary_from_rows(rows: list[Any]) -> str | None:
    patients: dict[str, dict[str, Any]] = {}
    current_pid: str | None = None

    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = row.get("fields")
        if not isinstance(fields, dict):
            fields = {}

        ref = row.get("fhir_ref")
        resource_type = row.get("resource_type")

        pid = fields.get("patient_id")
        if isinstance(pid, str) and pid:
            patients.setdefault(pid, {"pid": pid, "counts": {}, "problems": []})

        if isinstance(ref, str) and ref.startswith("Patient/"):
            current_pid = ref.removeprefix("Patient/")
            patient = patients.setdefault(
                current_pid, {"pid": current_pid, "counts": {}, "problems": []}
            )
            patient["given_name"] = fields.get("given_name") or ""
            patient["family_name"] = fields.get("family_name") or current_pid
            continue

        if isinstance(ref, str) and ref.startswith("count-summary:") and isinstance(pid, str):
            channel = str(fields.get("channel") or resource_type or "signals")
            count = int(fields.get("count") or 0)
            patient = patients.setdefault(pid, {"pid": pid, "counts": {}, "problems": []})
            patient["counts"][channel] = count
            continue

        if resource_type == "Condition" and current_pid:
            code = fields.get("code")
            if isinstance(code, str) and code:
                patient = patients.setdefault(
                    current_pid, {"pid": current_pid, "counts": {}, "problems": []}
                )
                patient["problems"].append(code)

    if not patients:
        return None

    ranked = sorted(
        patients.values(),
        key=lambda p: (
            sum(int(c) for c in (p.get("counts") or {}).values()),
            p.get("family_name") or "",
        ),
        reverse=True,
    )

    lines = ["Panel triage summary from your CareTeam roster:"]
    for idx, patient in enumerate(ranked, start=1):
        counts = patient.get("counts") or {}
        total = sum(int(c) for c in counts.values())
        name = " ".join(
            part
            for part in (
                str(patient.get("given_name") or "").strip(),
                str(patient.get("family_name") or patient.get("pid") or "").strip(),
            )
            if part
        )
        signal_bits = [
            f"{channel}: {count}"
            for channel, count in counts.items()
            if int(count) > 0
        ]
        signal_summary = ", ".join(signal_bits) if signal_bits else "no overnight signals"
        problem = (patient.get("problems") or ["no active problem returned"])[0]
        lines.append(
            f"{idx}. {name}: {total} overnight signal(s) ({signal_summary}); "
            f"active problem context: {problem}."
        )
    lines.append("Verify the source details in the chart before acting on the ranking.")
    return "\n".join(lines)


def _has_guideline_citation(citations: list[str]) -> bool:
    return any(c.startswith("guideline:") for c in citations)


def _has_clinical_claim(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _CLINICAL_CLAIM_PATTERNS)


def _has_evidence_gap_language(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in _EVIDENCE_GAP_PHRASES)


def _refs_from_tool_message(msg: ToolMessage) -> set[str]:
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    return _scrub_unresolvable_refs(set(_FHIR_REF_PATTERN.findall(content)))


def _cache_keys_from_tool_message(msg: ToolMessage) -> list[str]:
    """Return the cache_key for a cache-served ToolMessage, else empty.

    The extraction tool's cache-served envelope carries
    ``"cache_hit": true`` and ``"cache_key": "<key>"`` (see
    ``tools/extraction.py:_cache_row_envelope``). When the message has
    no ``cache_hit`` marker, returns ``[]``. When it does, returns the
    matching ``cache_key`` value (or a placeholder if the key is
    missing) so the per-turn ``cache_hits`` field has one entry per
    cache-served tool call.
    """

    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    if not _TOOL_CACHE_HIT_PATTERN.search(content):
        return []
    match = _TOOL_CACHE_KEY_PATTERN.search(content)
    return [match.group(1) if match else "cache_hit"]


# Issue 026: synthetic ``openemr-upload-<hex>`` document ids are legacy
# fallback identifiers (pre-issue-022) and are not real OpenEMR
# DocumentReference resources. Even when they sit in ``fetched_refs`` from
# prior-turn checkpointer state, the verifier must treat a citation
# against one as unresolved so a stale synthetic id cannot pass the
# citation gate as a real EHR document.
_SYNTHETIC_DOC_REF_PREFIX = "DocumentReference/openemr-upload-"


def _is_resolvable_citation_ref(ref: str) -> bool:
    """True when ``ref`` is a citation target the verifier can ratify.

    Accepted shapes:

    * ``ResourceType/id`` where ``id`` is a bare FHIR id, not a search URL
      or query-shaped pseudo-ref.
    * ``guideline:<chunk_id>`` retrieval refs.

    This deliberately rejects rows such as
    ``Observation/_summary=count?patient=fixture-3``: those are aggregate
    probe metadata from ``get_change_signal``, not fetched FHIR resources.
    """
    if not ref:
        return False
    if ref.startswith("guideline:"):
        return bool(ref.removeprefix("guideline:").strip())
    if ref.startswith(_SYNTHETIC_DOC_REF_PREFIX):
        return False
    return bool(_FHIR_RESOURCE_REF_PATTERN.fullmatch(ref))


def _scrub_unresolvable_refs(fetched: set[str]) -> set[str]:
    """Drop synthetic or query-shaped refs from a fetched-refs set."""
    return {ref for ref in fetched if _is_resolvable_citation_ref(ref)}


# Tool source labels that disambiguate Observation rows by FHIR category.
# Used to feed the citation-card mapper so cited Observation refs land on
# the right OpenEMR chart card.
_OBSERVATION_SOURCE_TO_CATEGORY = {
    "Observation (vital-signs)": "vital-signs",
    "Observation (laboratory)": "laboratory",
}


def _observation_categories_from_tool_message(
    msg: ToolMessage,
) -> dict[str, str]:
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    category: str | None = None
    for source_label, cat in _OBSERVATION_SOURCE_TO_CATEGORY.items():
        if source_label in content:
            category = cat
            break
    if category is None:
        return {}
    return {
        ref: category
        for ref in _FHIR_REF_PATTERN.findall(content)
        if ref.startswith("Observation/")
    }


def _gate_decision_for_tool_message(msg: ToolMessage) -> str:
    """Map a ToolMessage's payload to one ``AuthDecision`` value.

    ``careteam_denied`` / ``patient_context_mismatch`` / ``no_active_patient``
    map to themselves. Anything else (success or non-auth error) collapses
    to ``allowed`` — gate decisions only track authorization, not
    operational outcomes.
    """
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    error_match = _TOOL_ERROR_PATTERN.search(content)
    if error_match is None:
        return AuthDecision.ALLOWED.value
    error = error_match.group(1)
    if error in _AUTH_DECISIONS:
        return error
    return AuthDecision.ALLOWED.value


def _resolved_patients_from_tool_message(
    msg: ToolMessage,
) -> dict[str, dict[str, Any]]:
    """Extract newly-resolved patients from a ``resolve_patient`` ToolMessage.

    Returns a dict keyed on ``patient_id`` carrying the display fields the
    registry stores (given_name, family_name, birth_date). Only ``status:
    "resolved"`` payloads contribute — ambiguous, not_found, and clarify
    intentionally do not populate the registry because the LLM still owes
    the user a follow-up.
    """
    if (msg.name or "") != "resolve_patient":
        return {}
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    status_match = _TOOL_STATUS_PATTERN.search(content)
    if status_match is None or status_match.group(1) != "resolved":
        return {}
    # The payload is JSON; parse it for structured access.
    try:
        import json

        payload = json.loads(content)
    except (ValueError, TypeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in payload.get("patients") or []:
        pid = p.get("patient_id")
        if not pid:
            continue
        out[pid] = {
            "patient_id": pid,
            "given_name": p.get("given_name") or "",
            "family_name": p.get("family_name") or "",
            "birth_date": p.get("birth_date") or "",
        }
    return out


def _per_call_costs(state: CoPilotState, settings: Settings) -> list[CallCost]:
    """One ``CallCost`` per AIMessage with usage metadata, in order.

    LangChain populates ``AIMessage.usage_metadata`` with
    ``input_tokens`` / ``output_tokens`` / ``total_tokens`` (best effort —
    not every provider does). When the metadata is missing the message is
    skipped rather than counted as zero, so a partial answer doesn't
    silently inflate the per-turn rate-known total.

    The model name on each AIMessage is preferred when present (LangChain
    stores it under ``response_metadata.model_name`` / ``model``); we fall
    back to ``settings.llm_model`` so the audit row still names something
    when the provider didn't echo back.
    """
    out: list[CallCost] = []
    for msg in state.get("messages", []) or []:
        if not isinstance(msg, AIMessage):
            continue
        usage = getattr(msg, "usage_metadata", None) or {}
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        if in_tok == 0 and out_tok == 0:
            continue
        rmeta = getattr(msg, "response_metadata", None) or {}
        model = (
            rmeta.get("model_name")
            or rmeta.get("model")
            or settings.llm_model
        )
        out.append(
            estimate_call_cost(
                str(model),
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        )
    return out


def _tool_sequence(state: CoPilotState) -> list[str]:
    """Ordered list of tool names invoked this turn (duplicates kept).

    ``tool_results`` is the canonical record because ``agent_node`` already
    extracts ``{"name", "args", "id"}`` from each AIMessage's tool_calls.
    Falls back to scanning AIMessage.tool_calls directly when the state
    field is empty (e.g., refusal turns where no tool ran but the LLM
    still emitted a malformed tool_call).
    """
    tool_results = state.get("tool_results") or []
    if tool_results:
        return [str(tc.get("name") or "") for tc in tool_results if tc.get("name")]
    seq: list[str] = []
    for msg in state.get("messages", []) or []:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name:
                    seq.append(str(name))
    return seq


def _audit(
    state: CoPilotState,
    settings: Settings,
    *,
    decision: str,
    final_text: str | None = None,
    escalation_reason: str | None = None,
) -> None:
    """Write one ``agent_audit`` row for the turn.

    Free text (user prompt, assistant body) is intentionally NOT recorded
    here — that's the §9 step 11 "encrypted prompts/responses" table's job.
    The audit log carries only structural/decision metadata.

    ``extra.gate_decisions`` and ``extra.denied_count`` carry the per-turn
    gate-decision summary called out in issue 003. ``extra.tool_sequence``,
    ``extra.cost_estimate_usd``, and ``extra.cost_by_model`` carry the
    per-encounter trace data called out in issue 012.
    """
    tool_results = state.get("tool_results") or []
    fetched_refs = state.get("fetched_refs") or []
    user_messages = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
    gate_decisions = list(state.get("gate_decisions") or [])
    denied_count = sum(1 for d in gate_decisions if d in _DENIED_DECISIONS)

    call_costs = _per_call_costs(state, settings)
    turn_cost = aggregate_turn_cost(call_costs)
    prompt_tokens = sum(c.input_tokens for c in call_costs)
    completion_tokens = sum(c.output_tokens for c in call_costs)
    tool_sequence = _tool_sequence(state)

    extra: dict[str, Any] = {
        "final_response_chars": len(final_text) if final_text else 0,
        "gate_decisions": gate_decisions,
        "denied_count": denied_count,
        "tool_sequence": tool_sequence,
        "cost_estimate_usd": turn_cost.total_usd,
    }
    if turn_cost.by_model:
        extra["cost_by_model"] = turn_cost.by_model
    if turn_cost.rate_unknown_models:
        extra["cost_rate_unknown_models"] = turn_cost.rate_unknown_models

    # Issue 009 — when the supervisor sub-graph ran, surface its action
    # and reasoning plus the handoff trail so a reviewer can reconstruct
    # the dispatch path without re-running the pipeline. Absent for W1
    # turns by design.
    supervisor_action = state.get("supervisor_action")
    if supervisor_action:
        extra["supervisor_action"] = supervisor_action
        extra["supervisor_reasoning"] = state.get("supervisor_reasoning") or ""
    handoff_events = state.get("handoff_events") or []
    if handoff_events:
        extra["handoff_events"] = list(handoff_events)

    event = AuditEvent(
        ts=now_iso(),
        conversation_id=state.get("conversation_id") or "",
        user_id=state.get("user_id") or "",
        patient_id=state.get("focus_pid") or state.get("patient_id") or "",
        turn_index=len(user_messages),
        workflow_id=state.get("workflow_id") or "unclear",
        classifier_confidence=float(state.get("classifier_confidence") or 0.0),
        decision=decision,
        regen_count=int(state.get("regen_count") or 0),
        tool_call_count=len(tool_results),
        fetched_ref_count=len(fetched_refs),
        latency_ms=0,  # The graph doesn't track end-to-end latency yet; eval runner does.
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model_provider=settings.llm_provider,
        model_name=settings.llm_model,
        escalation_reason=escalation_reason,
        extra=extra,
    )
    write_audit_event(event, settings)


def build_graph(settings: Settings | None = None, *, checkpointer: Any | None = None):
    """Compile and return the agent graph.

    ``checkpointer`` is injected: callers that need durable persistence open
    an ``AsyncPostgresSaver`` via ``open_checkpointer(settings)`` and pass
    it in. Defaults to an in-process MemorySaver — fine for tests, scripts,
    and demos.
    """
    settings = settings or get_settings()
    chat_model = build_chat_model(settings)
    classifier_model = chat_model.with_structured_output(WorkflowDecision)
    tools = make_tools(settings)
    if checkpointer is None:
        checkpointer = build_memory_checkpointer()

    async def classifier_node(state: CoPilotState) -> Command:
        all_messages = state.get("messages", [])
        user_messages = [m for m in all_messages if isinstance(m, HumanMessage)]
        if not user_messages:
            return Command(
                goto="agent",
                update={"workflow_id": "unclear", "classifier_confidence": 0.0},
            )
        latest = user_messages[-1].content
        latest = latest if isinstance(latest, str) else str(latest)

        # The upload endpoint injects a ``[system] Document uploaded: …``
        # sentinel as a SystemMessage so the classifier prompt can route
        # to W-DOC (prompts.py:55). Surface the most-recent such sentinel
        # alongside the user's text so the classifier sees the context.
        upload_sentinel: str | None = None
        for msg in reversed(all_messages):
            if isinstance(msg, SystemMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.startswith("[system] Document uploaded:"):
                    upload_sentinel = content
                    break

        classifier_input = (
            f"{upload_sentinel}\n\n{latest}" if upload_sentinel else latest
        )

        patient_id = state.get("patient_id")
        focus_pid = state.get("focus_pid")

        try:
            decision = await classifier_model.ainvoke(
                [SystemMessage(content=CLASSIFIER_SYSTEM), HumanMessage(content=classifier_input)]
            )
        except Exception as exc:
            _log.warning(
                "classifier_failed model=%s err=%s: %s",
                settings.llm_model,
                exc.__class__.__name__,
                exc,
                exc_info=True,
            )
            # Fail open to ``agent`` when we already know which patient the
            # user is asking about; otherwise fall back to clarify.
            fallback = _route_after_classifier(
                workflow_id="unclear",
                confidence=0.0,
                patient_id=patient_id,
                focus_pid=focus_pid,
            )
            return Command(
                goto=fallback,
                update={
                    "workflow_id": "unclear",
                    "classifier_confidence": 0.0,
                    # Reset per-turn supervisor state so the hard guard
                    # in supervisor_node doesn't see iterations/refs
                    # from prior turns and force-synthesize without
                    # dispatching a worker (verifier then sees the
                    # user's HumanMessage as last and refuses).
                    "supervisor_iterations": 0,
                },
            )

        workflow_id = decision.workflow_id
        confidence = decision.confidence

        goto = _route_after_classifier(
            workflow_id=workflow_id,
            confidence=confidence,
            patient_id=patient_id,
            focus_pid=focus_pid,
        )
        return Command(
            goto=goto,
            update={
                "workflow_id": workflow_id,
                "classifier_confidence": confidence,
                # Reset per-turn supervisor state — see classifier
                # failure path above for the rationale.
                "supervisor_iterations": 0,
            },
        )

    async def clarify_node(state: CoPilotState) -> dict[str, Any]:
        user_messages = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
        latest = user_messages[-1].content if user_messages else ""
        latest_str = latest if isinstance(latest, str) else str(latest)
        try:
            response = await chat_model.ainvoke(
                [SystemMessage(content=CLARIFY_SYSTEM), HumanMessage(content=latest_str)]
            )
            content = (
                response.content
                if isinstance(response.content, str)
                else str(response.content)
            )
        except Exception:
            content = (
                "I'm not sure what you want to look at. Could you say which "
                "patient (or which question across your panel)?"
            )
        _audit(state, settings, decision="clarify", final_text=content)
        clarify_block = block_from_clarify_text(content)
        return {
            "messages": [AIMessage(content=content)],
            "decision": "clarify",
            "block": clarify_block.model_dump(by_alias=True),
        }

    async def agent_node(state: CoPilotState) -> dict[str, Any]:
        feedback = state.get("verifier_feedback") or ""
        smart_token = state.get("smart_access_token") or ""
        user_id = state.get("user_id") or ""
        registry = dict(state.get("resolved_patients") or {})
        focus_pid = state.get("focus_pid") or state.get("patient_id") or None

        # Bind context for the tool layer. ``set_active_registry`` lets
        # ``resolve_patient`` do O(1) cache hits on previously-resolved
        # names; the gate consults ``user_id`` directly.
        set_active_smart_token(smart_token or None)
        set_active_user_id(user_id or None)
        set_active_registry(registry)

        system_prompt = build_system_prompt(
            registry=registry,
            focus_pid=focus_pid,
            workflow_id=state.get("workflow_id") or "unclear",
            confidence=float(state.get("classifier_confidence") or 0.0),
        )
        if feedback:
            system_prompt += (
                "\n\nVERIFIER FEEDBACK FROM PRIOR ATTEMPT:\n"
                f"{feedback}\n"
                "Re-issue your response, citing only resources from the fetched set. "
                "If a claim cannot be supported by a fetched resource, drop the claim "
                "or explicitly state the gap."
            )

        agent = create_agent(model=chat_model, tools=tools, system_prompt=system_prompt)

        result = await agent.ainvoke({"messages": state.get("messages", [])})

        sub_messages = result.get("messages", [])
        fetched: list[str] = []
        tool_calls: list[dict] = []
        observation_categories: dict[str, str] = {}
        gate_decisions: list[str] = []
        cache_hits: list[str] = []
        new_resolved: dict[str, dict[str, Any]] = {}
        for msg in sub_messages:
            if isinstance(msg, ToolMessage):
                fetched.extend(_refs_from_tool_message(msg))
                observation_categories.update(
                    _observation_categories_from_tool_message(msg)
                )
                gate_decisions.append(_gate_decision_for_tool_message(msg))
                cache_hits.extend(_cache_keys_from_tool_message(msg))
                new_resolved.update(_resolved_patients_from_tool_message(msg))
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        {"name": tc.get("name"), "args": tc.get("args") or {}, "id": tc.get("id")}
                    )

        final = sub_messages[-1] if sub_messages else AIMessage(content="")
        final_text = final.content if isinstance(final.content, str) else str(final.content)

        # Carry forward the new focus: prefer the most recently resolved pid,
        # falling back to the prior focus when no resolution happened.
        new_focus = focus_pid
        if new_resolved:
            new_focus = next(reversed(new_resolved))

        update: dict[str, Any] = {
            "messages": [final],
            "fetched_refs": fetched,
            "tool_results": tool_calls,
            "observation_categories": observation_categories,
            "gate_decisions": gate_decisions,
            "cache_hits": cache_hits,
            "verifier_feedback": "",
        }
        if new_resolved:
            update["resolved_patients"] = new_resolved
        if new_focus and new_focus != state.get("focus_pid"):
            update["focus_pid"] = new_focus

        # Issue 042: panel-route fail-closed. ``state.messages`` doesn't
        # carry the inner agent's ToolMessages (only ``[final]`` is
        # appended by the reducer), so the panel-failure detection has
        # to run here where ``sub_messages`` is in scope. We replace
        # the answer + block with the panel-unavailable refusal and
        # set ``decision="tool_failure"`` — the verifier's short-circuit
        # at the top of ``verifier_node`` then exits cleanly without
        # treating this as an unresolved-citation regen case.
        panel_path = (state.get("workflow_id") or "") == "W-1" or _has_panel_tool_call(
            sub_messages
        )
        panel_tool_failed = _has_failed_panel_tool_message(sub_messages)
        if panel_path and (panel_tool_failed or _has_internal_leak(final_text)):
            refusal_text = (
                "Panel data is unavailable right now, so I can't rank the "
                "patients on your panel. Please retry in a moment, or pick "
                "a specific patient to ask about instead."
            )
            refusal_block = refusal_plain_block(refusal_text)
            update["messages"] = [AIMessage(content=refusal_text)]
            update["decision"] = "tool_failure"
            update["block"] = refusal_block.model_dump(by_alias=True)
            return update

        panel_summary = (
            _panel_triage_summary_from_tool_messages(sub_messages)
            if panel_path
            else None
        )
        if panel_summary:
            final_text = panel_summary
            update["messages"] = [AIMessage(content=panel_summary)]

        # Synthesize the structured overnight block. Validation failures fall
        # back to a PlainBlock inside the helper so the wire shape is always
        # valid even if structured-output parsing breaks.
        block = await synthesize_overnight_block(
            chat_model,
            synthesis_text=final_text,
            fetched_refs=fetched,
            observation_categories=observation_categories,
        )
        update["block"] = block.model_dump(by_alias=True)
        return update

    def verifier_node(state: CoPilotState) -> Command:
        # If the agent_node already set a hard-deny decision (e.g. patient
        # context mismatch), preserve it — verification semantics don't apply.
        existing_decision = state.get("decision")
        if existing_decision in {"denied_authz", "tool_failure", "blocked_baa", "refused_safety"}:
            _audit(state, settings, decision=existing_decision)
            return Command(goto=END)

        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage):
            _audit(state, settings, decision="tool_failure")
            failure_block = refusal_plain_block(
                "I couldn't produce a verifiable response. Please retry."
            )
            return Command(
                goto=END,
                update={
                    "decision": "tool_failure",
                    "block": failure_block.model_dump(by_alias=True),
                },
            )

        text = last.content if isinstance(last.content, str) else str(last.content)
        citations = _extract_citations(text)
        # Issue 026: scrub legacy synthetic upload ids before computing
        # unresolved. A cite against ``DocumentReference/openemr-upload-<hex>``
        # never counts as fetched, even when prior-turn state carried the
        # id forward via the checkpointer.
        fetched = _scrub_unresolvable_refs(set(state.get("fetched_refs") or []))
        unresolved = [c for c in citations if c not in fetched]

        # Issue 041: fail-closed when guideline retrieval itself failed
        # OR when the synthesizer leaked internal markers (worker names,
        # raw error tokens, HTTP statuses) into the user-facing answer.
        # Either condition makes the corpus-bound contract impossible to
        # honor honestly, so we replace the answer with a clean
        # limitation message regardless of what the LLM wrote.
        if _is_evidence_path(state) and (
            _evidence_retrieval_failed(state) or _has_internal_leak(text)
        ):
            refusal_text = (
                "I couldn't reach the clinical guideline corpus this turn, "
                "so I won't offer a recommendation. The answer would not "
                "be grounded in retrieved guideline evidence. Please retry "
                "in a moment, or consult the guideline directly."
            )
            refusal = AIMessage(content=refusal_text)
            refusal_block = refusal_plain_block(refusal_text)
            _audit(
                state,
                settings,
                decision="refused_unsourced",
                escalation_reason="evidence_retrieval_failed",
            )
            return Command(
                goto=END,
                update={
                    "messages": [refusal],
                    "decision": "refused_unsourced",
                    "block": refusal_block.model_dump(by_alias=True),
                },
            )

        # Issue 028: fail-closed for W-EVD answers that assert clinical
        # recommendations without a ratified guideline citation. We check
        # this before the "all resolved" branch so an answer that cites
        # only chart refs (FHIR Observation, etc.) on a guideline-intent
        # turn still gets rejected for the missing guideline citation.
        if (
            _is_evidence_path(state)
            and not _has_guideline_citation(citations)
            and _has_clinical_claim(text)
            and not _has_evidence_gap_language(text)
        ):
            refusal_text = (
                "I couldn't ground this recommendation against the clinical "
                "guideline corpus available in this turn. No citeable "
                "guideline evidence was retrieved, so I won't offer an "
                "uncited recommendation. Please rephrase the question or "
                "consult the guideline directly."
            )
            refusal = AIMessage(content=refusal_text)
            refusal_block = refusal_plain_block(refusal_text)
            _audit(
                state,
                settings,
                decision="refused_unsourced",
                escalation_reason="uncited_guideline_claim",
            )
            return Command(
                goto=END,
                update={
                    "messages": [refusal],
                    "decision": "refused_unsourced",
                    "block": refusal_block.model_dump(by_alias=True),
                },
            )

        # Issue 035: fail-closed for W-DOC answers that emit clinical
        # claims with zero citations. A document-sourced value must
        # always carry a ``DocumentReference/...`` cite — otherwise the
        # synthesizer is presenting an extracted lab as chart truth
        # without provenance. Mirrors the W-EVD pattern: immediate
        # refuse when the text both makes a clinical claim and skips
        # the evidence-gap admission language.
        if (
            _is_document_path(state)
            and not citations
            and _has_clinical_claim(text)
            and not _has_evidence_gap_language(text)
        ):
            refusal_text = (
                "I couldn't ground the document-derived clinical claim(s) "
                "in this turn against any cited source. Without a "
                "DocumentReference citation I won't assert an extracted "
                "value as chart truth — please verify the value directly "
                "in the source document or rephrase the question."
            )
            refusal = AIMessage(content=refusal_text)
            refusal_block = refusal_plain_block(refusal_text)
            _audit(
                state,
                settings,
                decision="refused_unsourced",
                escalation_reason="uncited_document_claim",
            )
            return Command(
                goto=END,
                update={
                    "messages": [refusal],
                    "decision": "refused_unsourced",
                    "block": refusal_block.model_dump(by_alias=True),
                },
            )

        if not unresolved:
            _audit(state, settings, decision="allow", final_text=text)
            # Build a fresh PlainBlock from this turn's AIMessage so the
            # UI doesn't render a stale ``block`` left over from a prior
            # turn (e.g., a previous regen-refusal). The supervisor path
            # doesn't synthesize a block itself, so without this update
            # the wire stays pinned to whatever block was set last.
            #
            # Issue 027: ratify the cite tags into Citation objects so
            # guideline / FHIR / document refs survive the trip to the
            # frontend as visible source chips. ``build_citations`` drops
            # any ref not in ``fetched`` — but we already proved
            # ``unresolved`` is empty, so every cited ref makes it.
            ratified_citations = build_citations(
                cited_refs=citations,
                fetched_refs=fetched,
                observation_categories=state.get("observation_categories") or {},
                cite_attributes=extract_cite_attributes(text),
            )
            fresh_block = plain_block_from_text(
                text, citations=ratified_citations
            )
            return Command(
                goto=END,
                update={
                    "decision": "allow",
                    "block": fresh_block.model_dump(by_alias=True),
                },
            )

        regen = state.get("regen_count") or 0
        if regen >= MAX_REGENS:
            refusal_text = (
                "I couldn't ground the following claim(s) against the chart data "
                f"available in this turn: {', '.join(unresolved)}. "
                "These refs do not match any FHIR resource I fetched. "
                "Please rephrase or verify directly in the chart."
            )
            refusal = AIMessage(content=refusal_text)
            refusal_block = refusal_plain_block(refusal_text)
            _audit(
                state,
                settings,
                decision="refused_unsourced",
                escalation_reason=f"unresolved_citations={unresolved}",
            )
            return Command(
                goto=END,
                update={
                    "messages": [refusal],
                    "decision": "refused_unsourced",
                    "block": refusal_block.model_dump(by_alias=True),
                },
            )

        feedback = (
            f"CITATION ERROR: Your prior response cited {unresolved}, which do NOT "
            "exist in any tool result you received this turn. You hallucinated "
            "those references. "
            f"\n\nThe ONLY fetched refs you may cite are: {sorted(fetched)}. "
            "\n\nWhen you redraft:"
            "\n  1. Cite ONLY refs from the fetched list. If a value (BP, lab, dose) "
            "doesn't have a corresponding fetched ref, do NOT state the value — "
            "describe the gap instead (e.g., 'a hypotensive episode is mentioned in "
            "the cross-cover note <cite ref=\"DocumentReference/...\"/>; the "
            "underlying Observation was not retrieved this turn')."
            "\n  2. Do not invent IDs even if a plausible-sounding one fits the "
            "narrative. Plausibility is not existence."
            "\n  3. If you cannot answer the question with the fetched refs, say so "
            "explicitly and stop."
        )
        return Command(
            goto="agent",
            update={"regen_count": regen + 1, "verifier_feedback": feedback},
        )

    # Issue 009: supervisor sub-graph for W-DOC / W-EVD intents. The
    # workers' tool surfaces are subsets of ``tools`` filtered by name,
    # so they're cheap to build at compile time and run with the same
    # CareTeam-gated client wiring as the main agent.
    supervisor_node = build_supervisor_node(chat_model)
    intake_extractor_node = build_intake_extractor_node(chat_model, tools)
    evidence_retriever_node = build_evidence_retriever_node(chat_model, tools)

    builder = StateGraph(CoPilotState)
    builder.add_node(
        "classifier",
        classifier_node,
        ends=["agent", "clarify", "supervisor"],
    )
    builder.add_node("clarify", clarify_node)
    builder.add_node("agent", agent_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("intake_extractor", intake_extractor_node)
    builder.add_node("evidence_retriever", evidence_retriever_node)
    builder.add_node("verifier", verifier_node, ends=["agent", END])
    builder.add_edge(START, "classifier")
    builder.add_edge("clarify", END)
    builder.add_edge("agent", "verifier")
    # After the supervisor decides, dispatch to the worker, the verifier
    # (synthesize), or the clarify node — same conditional pattern as
    # the classifier above.
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "intake_extractor": "intake_extractor",
            "evidence_retriever": "evidence_retriever",
            "verifier": "verifier",
            "clarify": "clarify",
        },
    )
    # Workers loop back to the supervisor so it can synthesize once
    # results are in. The synthesize action then routes to the verifier.
    builder.add_edge("intake_extractor", "supervisor")
    builder.add_edge("evidence_retriever", "supervisor")
    return builder.compile(checkpointer=checkpointer)


# Compatibility re-exports so callers that import the constants for tests
# continue to work — both are advisory now and used only for clarify-route
# decisioning.
__all__ = [
    "CLASSIFIER_CONFIDENCE_THRESHOLD",
    "MAX_REGENS",
    "WorkflowDecision",
    "build_graph",
]
