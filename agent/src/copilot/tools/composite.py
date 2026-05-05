"""Composite workflow tools — 7 multi-resource fan-out tools.

Each composite fans out multiple granular reads in parallel via
``asyncio.gather`` and merges results into one envelope shaped exactly
like a granular tool's ``ToolResult.to_payload()``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain_core.tools import StructuredTool

from ..care_team import CareTeamGate
from ..fhir import ToolResult
from .helpers import (
    _enforce_patient_authorization,
    _hours_until_now_from_iso,
    _merge_envelopes,
    _merge_panel_envelopes,
    get_active_user_id,
)


def make_composite_tools(
    gate: CareTeamGate,
    callables: dict[str, Any],
) -> list[StructuredTool]:
    """Build the 7 composite tools using granular callables.

    ``callables`` is the dict returned by ``make_granular_tools`` mapping
    function names to their raw async functions.
    """
    get_change_signal = callables["get_change_signal"]
    get_patient_demographics = callables["get_patient_demographics"]
    get_active_problems = callables["get_active_problems"]
    get_active_medications = callables["get_active_medications"]
    get_recent_vitals = callables["get_recent_vitals"]
    get_recent_labs = callables["get_recent_labs"]
    get_recent_encounters = callables["get_recent_encounters"]
    get_recent_orders = callables["get_recent_orders"]
    get_imaging_results = callables["get_imaging_results"]
    get_medication_administrations = callables["get_medication_administrations"]
    get_clinical_notes = callables["get_clinical_notes"]

    async def run_panel_triage(hours: int = 24) -> dict[str, Any]:
        """Composite tool: rank the user's CareTeam panel by overnight signal."""
        user_id = get_active_user_id() or ""
        roster = await gate.list_panel(user_id)

        if not roster:
            return ToolResult(
                ok=True,
                rows=(),
                sources_checked=("CareTeam (panel)",),
                latency_ms=0,
            ).to_payload()

        started = time.monotonic()

        async def _per_pid(pid: str) -> tuple[dict[str, Any], ...]:
            return await asyncio.gather(
                get_change_signal(pid, hours),
                get_patient_demographics(pid),
                get_active_problems(pid),
            )

        nested = await asyncio.gather(*[_per_pid(p.patient_id) for p in roster])
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_panel_envelopes(
            list(nested), panel_source="CareTeam (panel)", elapsed_ms=elapsed_ms
        )

    async def run_panel_med_safety(hours: int = 24) -> dict[str, Any]:
        """Composite tool: pharmacist-style med-safety scan across the panel."""
        user_id = get_active_user_id() or ""
        roster = await gate.list_panel(user_id)

        if not roster:
            return ToolResult(
                ok=True,
                rows=(),
                sources_checked=("CareTeam (panel)",),
                latency_ms=0,
            ).to_payload()

        started = time.monotonic()

        async def _per_pid(pid: str) -> tuple[dict[str, Any], ...]:
            return await asyncio.gather(
                get_active_medications(pid),
                get_recent_labs(pid, hours),
            )

        nested = await asyncio.gather(*[_per_pid(p.patient_id) for p in roster])
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_panel_envelopes(
            list(nested), panel_source="CareTeam (panel)", elapsed_ms=elapsed_ms
        )

    async def run_per_patient_brief(
        patient_id: str, hours: int = 24
    ) -> dict[str, Any]:
        """Composite tool: fan out the six per-patient brief reads in parallel."""
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        started = time.monotonic()
        results = await asyncio.gather(
            get_patient_demographics(patient_id),
            get_active_problems(patient_id),
            get_active_medications(patient_id),
            get_recent_vitals(patient_id, hours),
            get_recent_labs(patient_id, hours),
            get_recent_encounters(patient_id, hours),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_envelopes(list(results), elapsed_ms=elapsed_ms)

    async def run_cross_cover_onboarding(
        patient_id: str, hours: int = 168
    ) -> dict[str, Any]:
        """Composite tool: hospital-course orientation for cross-cover / family meetings."""
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        started = time.monotonic()
        results = await asyncio.gather(
            get_active_problems(patient_id),
            get_active_medications(patient_id),
            get_recent_encounters(patient_id, hours),
            get_recent_orders(patient_id, hours),
            get_clinical_notes(patient_id, hours),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_envelopes(list(results), elapsed_ms=elapsed_ms)

    async def run_abx_stewardship(
        patient_id: str, hours: int = 72
    ) -> dict[str, Any]:
        """Composite tool: antibiotic stewardship review for ONE patient."""
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        started = time.monotonic()
        results = await asyncio.gather(
            get_active_medications(patient_id),
            get_medication_administrations(patient_id, hours),
            get_recent_labs(patient_id, hours),
            get_recent_orders(patient_id, hours),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_envelopes(list(results), elapsed_ms=elapsed_ms)

    async def run_consult_orientation(
        patient_id: str, domain: str, hours: int = 168
    ) -> dict[str, Any]:
        """Composite tool: consult orientation scoped to a clinical domain."""
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        normalized = (domain or "").strip().lower()
        branch_factories: dict[str, list[Any]] = {
            "cardiology": [
                lambda: get_active_problems(patient_id),
                lambda: get_active_medications(patient_id),
                lambda: get_recent_vitals(patient_id, hours),
                lambda: get_recent_labs(patient_id, hours),
                lambda: get_recent_encounters(patient_id, hours),
                lambda: get_imaging_results(patient_id, hours),
                lambda: get_clinical_notes(patient_id, hours),
            ],
            "nephrology": [
                lambda: get_active_problems(patient_id),
                lambda: get_active_medications(patient_id),
                lambda: get_recent_labs(patient_id, hours),
                lambda: get_recent_encounters(patient_id, hours),
                lambda: get_medication_administrations(patient_id, hours),
                lambda: get_clinical_notes(patient_id, hours),
            ],
            "id": [
                lambda: get_active_problems(patient_id),
                lambda: get_active_medications(patient_id),
                lambda: get_medication_administrations(patient_id, hours),
                lambda: get_recent_labs(patient_id, hours),
                lambda: get_recent_orders(patient_id, hours),
                lambda: get_clinical_notes(patient_id, hours),
            ],
        }
        factories = branch_factories.get(normalized)
        if factories is None:
            return ToolResult(
                ok=False,
                sources_checked=(),
                error="invalid_domain",
                latency_ms=0,
            ).to_payload()

        started = time.monotonic()
        results = await asyncio.gather(*[factory() for factory in factories])
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_envelopes(list(results), elapsed_ms=elapsed_ms)

    async def run_recent_changes(
        patient_id: str, since: str
    ) -> dict[str, Any]:
        """Composite tool: diff over time-windowed resources since a cutoff."""
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        hours = _hours_until_now_from_iso(since)
        if hours is None:
            return ToolResult(
                ok=False,
                sources_checked=(),
                error="invalid_since",
                latency_ms=0,
            ).to_payload()

        started = time.monotonic()
        results = await asyncio.gather(
            get_recent_vitals(patient_id, hours),
            get_recent_labs(patient_id, hours),
            get_recent_encounters(patient_id, hours),
            get_recent_orders(patient_id, hours),
            get_imaging_results(patient_id, hours),
            get_medication_administrations(patient_id, hours),
            get_clinical_notes(patient_id, hours),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_envelopes(list(results), elapsed_ms=elapsed_ms)

    return [
        StructuredTool.from_function(
            coroutine=run_per_patient_brief,
            name="run_per_patient_brief",
            description=(
                "Composite per-patient brief: fans out demographics, active "
                "problems, active medications, recent vitals (24h), recent "
                "labs (24h), and recent encounters (24h) in PARALLEL and "
                "returns one merged envelope. Prefer this tool over the "
                "granular reads whenever the user asks for an overview, a "
                "brief, a quick picture, or 'what happened to <patient>' — "
                "it is materially faster than chaining the six granular "
                "calls. Returns the same envelope shape (rows, "
                "sources_checked, latency_ms, error, ok) so citation "
                "verification is unchanged. For a single targeted question "
                "(one specific lab, one specific vital), use the granular "
                "tool instead."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_panel_triage,
            name="run_panel_triage",
            description=(
                "Composite panel triage: rank the user's CareTeam panel by "
                "overnight signal so the clinician knows who to see first. "
                "Implementation: list_panel → per-pid parallel fan-out of "
                "change-signal counts (vitals, labs, encounters, document "
                "refs in the lookback window, default 24h), demographics, "
                "and active problems. Use this tool whenever the user asks "
                "about prioritization across the panel — 'who needs "
                "attention?', 'who do I need to see first?', 'anyone "
                "deteriorating?', 'morning rounds order'. Returns the same "
                "envelope shape (rows, sources_checked, latency_ms, error, "
                "ok) as a granular tool. The roster is intrinsically "
                "CareTeam-bounded; no patient outside the user's panel can "
                "appear. No arguments other than an optional ``hours`` "
                "lookback window."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_panel_med_safety,
            name="run_panel_med_safety",
            description=(
                "Composite panel med-safety scan: pharmacist-style review "
                "across the user's CareTeam panel. Implementation: "
                "list_panel → per-pid parallel fan-out of active "
                "medications and recent labs (default 24h). Use this tool "
                "whenever the user asks about medication-safety review "
                "across the panel — 'which patients need a med review?', "
                "'any pharmacy concerns this morning?', 'who's on a renally "
                "cleared med with rising creatinine?', 'anyone hyperkalemic "
                "on an ACE?'. The returned envelope contains all active "
                "meds plus all recent labs per patient; the synthesis "
                "framing applies the renal / hepatic / anticoagulant lens "
                "and surfaces the safety-relevant combinations. Returns "
                "the same envelope shape (rows, sources_checked, "
                "latency_ms, error, ok) as a granular tool. The roster is "
                "intrinsically CareTeam-bounded; no patient outside the "
                "user's panel can appear. No arguments other than an "
                "optional ``hours`` lookback window. For a single-patient "
                "med-safety question (e.g., 'should THIS patient still be "
                "on broad-spectrum?'), use the granular tools instead."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_cross_cover_onboarding,
            name="run_cross_cover_onboarding",
            description=(
                "Composite hospital-course orientation for ONE patient: "
                "fans out active problems, active medications, recent "
                "encounters, recent orders (ServiceRequest), and clinical "
                "notes (DocumentReference) over a wider lookback window "
                "(default 168 hours / 7 days) in PARALLEL and returns one "
                "merged envelope. Prefer this tool whenever the user asks "
                "to be oriented on a patient they haven't met — "
                "'I'm cross-covering, get me up to speed on Hayes', "
                "'I've never seen this patient — what do I need to know?', "
                "'I'm meeting with the family this afternoon, what's the "
                "story?', 'walk me through this admission'. Same data "
                "shape works for both cross-cover (W-4) and "
                "family-meeting prep (W-5); the synthesis framing handles "
                "the rest. Returns the same envelope shape (rows, "
                "sources_checked, latency_ms, error, ok) as a granular "
                "tool. For a 24-hour overnight brief, use "
                "``run_per_patient_brief`` instead — it pulls vitals and "
                "labs that the cross-cover composite intentionally omits."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_recent_changes,
            name="run_recent_changes",
            description=(
                "Composite diff for ONE patient: fans out the seven "
                "time-windowed reads — vitals, labs, encounters, orders, "
                "imaging results, medication administrations, and "
                "clinical notes — over a window starting at ``since`` (an "
                "ISO 8601 timestamp like '2026-04-25T08:00:00Z') in "
                "PARALLEL and returns one merged envelope of everything "
                "new since that cutoff. Prefer this tool whenever the "
                "user asks what's changed, what's new, what they missed, "
                "or what happened since a specific time — 'what's new on "
                "Hayes since rounds?', 'anything happen since I left at "
                "4pm?', 'diff me on Eduardo since yesterday', 'I last "
                "looked at this chart Tuesday — what changed?'. Active "
                "problems and active medications are intentionally NOT "
                "in the diff — they're current state, not changes; "
                "fetch them granularly if you need to anchor against "
                "the current med list. Returns the same envelope shape "
                "(rows, sources_checked, latency_ms, error, ok) as a "
                "granular tool. Malformed or future ``since`` values "
                "return ``error='invalid_since'``."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_consult_orientation,
            name="run_consult_orientation",
            description=(
                "Composite consult-orientation read for ONE patient, "
                "scoped by clinical ``domain``. The domain selects the "
                "fan-out shape so the consulting specialist sees what "
                "their service reads against. Supported domains: "
                "``cardiology`` (problems + meds + vitals + labs + "
                "encounters + imaging + notes — vitals and imaging "
                "are cardiology-specific because BP/HR trends and "
                "echo/cath conclusions matter), ``nephrology`` "
                "(problems + meds + labs + encounters + medication "
                "administrations + notes — MARs surface held "
                "nephrotoxic doses), ``id`` (problems + meds + "
                "medication administrations + labs + orders + notes — "
                "ServiceRequest holds the culture orders, Observation "
                "laboratory holds the cultures). Default lookback is "
                "168 hours (7 days) so the envelope spans the "
                "admission, not just overnight. Prefer this tool "
                "whenever the user is preparing for a consult or "
                "asking the chart from a consultant's perspective — "
                "'cardiology consult on Hayes', 'orient me as nephro "
                "on Eduardo', 'walk me through Linda's chart from an "
                "ID standpoint'. Unknown / empty ``domain`` returns "
                "``error='invalid_domain'``. Returns the same envelope "
                "shape (rows, sources_checked, latency_ms, error, ok) "
                "as a granular tool. For a 24-hour overnight brief, "
                "use ``run_per_patient_brief``; for cross-cover "
                "orientation without a specialist lens, use "
                "``run_cross_cover_onboarding``."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_abx_stewardship,
            name="run_abx_stewardship",
            description=(
                "Composite antibiotic-stewardship review for ONE patient: "
                "fans out active medications, medication administrations, "
                "recent labs (Observation laboratory — where culture "
                "sensitivities, gram stains, and WBC trends live), and "
                "recent orders (ServiceRequest — where culture orders "
                "were authored) over a wider lookback window (default "
                "72 hours / 3 days) in PARALLEL and returns one merged "
                "envelope. Prefer this tool whenever the user asks an "
                "antibiotic-specific question for a single patient — "
                "'should this patient still be on broad-spectrum?', "
                "'is Hayes still on vanc/zosyn?', 'time to de-escalate "
                "Eduardo's antibiotics?', 'what's growing on Linda's "
                "cultures and is the abx coverage right?', 'how many "
                "days has she been on cefepime?'. The composite returns "
                "ALL active meds / MARs / labs / orders — the synthesis "
                "framing applies the antibiotic / culture / WBC lens. "
                "Active problems and demographics are intentionally NOT "
                "in the fan-out; fetch them granularly if you need to "
                "anchor the indication or compute a precise duration. "
                "Returns the same envelope shape (rows, sources_checked, "
                "latency_ms, error, ok) as a granular tool. For a "
                "panel-level med-safety scan (multiple patients), use "
                "``run_panel_med_safety`` instead."
            ),
        ),
    ]
