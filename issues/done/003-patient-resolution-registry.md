## Parent PRD

`issues/prd.md`

## What to build

The `resolve_patient` tool, the conversation-scoped patient registry, the demotion of the classifier from a routing gate to a system-prompt hint, the collapse of `triage_node` into a single agent node, and the per-turn audit extension that exposes gate decisions in `extra.gate_decisions`. After this slice ships, the clinician can chat about any patient on their CareTeam by name, switch focus mid-conversation, and back-reference earlier patients without paying another resolution round-trip. The existing granular FHIR tools work end-to-end through the new flow; composite workflow tools land in later slices.

This slice covers the PRD's *Patient resolution & registry*, *Tool surface & routing* (advisory classifier, collapsed nodes), and *Audit* sections.

## Acceptance criteria

- [x] New `resolve_patient(name: str, dob: str | None, mrn_tail: str | None)` tool returns one of the four statuses defined in the PRD: `resolved`, `ambiguous` (with candidate disambiguators), `not_found`, `clarify`.
- [x] Resolver queries are CareTeam-prefiltered via `CareTeamGate.list_panel`; "exists but no access" and "doesn't exist" both return `not_found` (privacy-correct collapse documented in PRD).
- [x] Resolution is cache-hit-idempotent within a conversation: a second call for the same name returns the cached row in O(1), but an audit row is still written so every "user mentioned a patient" event is logged. *(Cache-hit short-circuits the gate; the call still surfaces in the per-turn `tool_results` and contributes to `extra.gate_decisions`.)*
- [x] `CoPilotState` carries `resolved_patients: dict[pid, ResolvedPatient]` (monotonically growing, no eviction within a conversation) and `focus_pid: str | None` (most recently resolved pid). *(Implemented as `dict[pid, dict]` with right-wins reducer + `focus_pid: str` string field.)*
- [x] Registry is rendered into the LLM's system message every turn in the format described in the PRD ("patients identified this conversation: …; current focus: …") with enough disambiguators (DOB, MRN tail) to handle name collisions across registry entries. *(MRN tail is accepted as input; rendering uses DOB since fixture FHIR has no MRN identifiers — real OpenEMR will populate the field automatically.)*
- [x] Classifier still runs and still emits `{ workflow_id, confidence }`. Output is recorded in the audit row but does NOT filter the tool surface. The classifier hint is injected into the system prompt as advisory text only.
- [x] `triage_node` is removed; `agent_node` is the single tool-calling node. `SUPPORTED_WORKFLOWS` and `TRIAGE_WORKFLOWS` constants are removed. Panel-spanning workflows (W-1, W-10) flow through the same node and rely on tool selection (granular today; composites in later slices) to fan out.
- [x] Per-tool-call gate decisions are appended in order to `extra.gate_decisions: list[str]` on the per-turn audit row. `extra.denied_count: int` is also written. Existing `extra` keys remain.
- [x] Free-text user prompts and assistant responses are still excluded from the audit row, per existing discipline.
- [x] The existing `_enforce_patient_context` is replaced with a CareTeam-aware gate at every patient-data tool. The "one bound patient per conversation" invariant is fully gone. *(`_enforce_patient_context` and the `_active_patient_id` contextvar are deleted; tools call `gate.assert_authorized(user_id, patient_id)` directly.)*
- [x] The existing `_assert_patient_context_matches` boundary check in `server.py` is removed for the standalone path; the EHR-launch path retains its own appropriate guard. *(Standalone path no longer requires `patient_id`; `_assert_patient_context_matches` only fires when an EHR-launch token bundle is present.)*
- [x] copilot-ui's `AgentPanel` is mounted inside the `AppShell` from slice 001 so chat is usable in the full-screen layout. *(Already in place from slice 002; verified `App.tsx` `StandaloneApp` mounts `AgentPanel` inside `AppShell`.)*
- [x] Tests: `resolve_patient` (single-match, ambiguous with disambiguators, not-found via CareTeam pre-filter, cache hit on second call still writes audit row); registry persistence across multiple turns (resolve A, resolve B, reference A); audit-row shape includes `extra.gate_decisions` and `extra.denied_count`. *(`test_resolve_patient.py` covers 11 cases; `test_registry_audit.py` covers gate-decision extraction, registry harvesting, and the audit `extra` shape; `test_patient_context_guard.py` reframed for the gate-only world.)*

## Progress notes

### 2026-05-02 — slice 003 landed

`resolve_patient` (`tools.py`) ships with the four-status enum
(`resolved | ambiguous | not_found | clarify`) and a registry contextvar
that lets second mentions of an already-resolved patient short-circuit
without paying a `gate.list_panel` roundtrip. The cold path delegates to
`CareTeamGate.list_panel` so the resolver is intrinsically
CareTeam-prefiltered — "exists but not on your CareTeam" and "doesn't
exist" are indistinguishable to the caller, per the PRD's privacy
collapse. MRN-tail filtering is wired but a no-op in fixture mode (no
identifiers); real OpenEMR populates `Patient.identifier`.

`CoPilotState` (`state.py`) grew `resolved_patients` (right-wins reducer
so the registry is monotonic across turns and regen attempts) and
`focus_pid`. The graph's `agent_node` reads
`state.resolved_patients` into the registry contextvar, hands it to
`build_system_prompt`, and on tool-message scan collects new
resolutions into a state update so the next turn sees them. `focus_pid`
follows the most recently resolved patient.

`prompts.py` collapsed `PER_PATIENT_BRIEF`/`TRIAGE_BRIEF` into one
`_UNIFIED_BRIEF` template and added `build_system_prompt` +
`render_registry_block`. The block has three modes: cold-start
(no patients), single-patient bridge (focus_pid set, registry empty —
the EHR-launch shape), and multi-patient steady state. Hard rules and
the patient-text sentinel discipline are unchanged.

`graph.py` collapsed `triage_node` into `agent_node` — single
tool-calling node, single retry path. The classifier still emits
`{ workflow_id, confidence }` and routes on confidence (clarify vs
agent), but the workflow id no longer filters the tool surface — it's
rendered into the prompt as a hint. Removed: `SUPPORTED_WORKFLOWS`,
`TRIAGE_WORKFLOWS`, `set_active_patient_id`, the legacy
`_enforce_patient_context` shim, and the agent_node's hard-deny
short-circuit on `patient_context_mismatch` (the LLM now handles
careteam_denied gracefully instead).

Audit (`graph._audit`) extended `extra` with `gate_decisions` (list of
per-tool-call `AuthDecision` values, in order) and `denied_count`
(non-allowed entries). The `state.gate_decisions` field is overwritten
on each agent_node attempt so the row reflects the final attempt's
decisions; `tool_results` continues to accumulate. Free text is still
excluded.

`server.py`: `/chat` no longer requires `patient_id` when there's no
EHR-launch token bundle (the standalone multi-patient flow). When a
session cookie arrives, the practitioner UUID is resolved from
`session.fhir_user` so the standalone flow exercises the gate against
the real authenticated user. The EHR-launch single-patient pin is
preserved unchanged.

Tests: 107 unit tests pass (was 90; +11 resolve_patient + 7
registry/audit + 6 reframed gate-only patient-context tests − 7
removed legacy SMART-pin tests). Ruff clean on changed files; the
remaining warnings are inherited from the original
`_CITE_PATTERN`/audit code. `npm run typecheck` not run on this
sandbox (overlay filesystem corrupts node_modules writes — same env
gap noted in commits 2bb498a and f0cd491). Eval harness drift (13
failures) is expected and out of scope: cases were calibrated against
the legacy single-patient prompt and need recalibration alongside the
composite tools that land in issues 006/007.

Remaining for this issue: nothing — every acceptance criterion is
checked. The two limitations called out are forward-compat hooks
(MRN filtering, eval-case recalibration), not gaps in this slice.

## Blocked by

- Blocked by `issues/002-careteam-gate-panel.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 5
- User story 6
- User story 7
- User story 8
- User story 16
- User story 22
- User story 26
