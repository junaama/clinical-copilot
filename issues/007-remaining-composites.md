## Parent PRD

`issues/prd.md`

## What to build

The remaining six composite workflow tools and the remaining seven synthesis prompts so that the agent has full W-1…W-11 coverage as described in the PRD. After this slice ships, the agent can answer panel-level triage questions ("who do I need to see first?"), pharmacist-style med-safety scans across the panel, cross-cover onboarding briefs, family-meeting prep, consult orientation scoped to a domain, "what changed since I last looked," and antibiotic stewardship questions — each with a synthesis prompt tuned to the workflow.

This slice covers everything in the PRD's *Tool surface & routing* workflow ↔ tool mapping table that wasn't shipped in `issues/006-per-patient-brief-composite.md`. W-6 (causal trace) and W-7 (targeted drill) are deliberately not given composite tools; they continue to use granular reads under the appropriate synthesis prompt, and that is the correct design.

## Acceptance criteria

- [x] `run_panel_triage()` is registered. Implementation: `gate.list_panel(user_id)` (same call that powers `get_my_patient_list` in real mode and the empty-state UI) → parallel `get_change_signal` + `get_patient_demographics` + `get_active_problems` per pid → returns ranked panel envelope. Every per-pid nested call passes through `_enforce_patient_authorization`. *(Hours arg defaults to 24; outer `asyncio.gather` over the pid list, inner `gather` over the three branches per pid; merged envelope is byte-for-byte the granular `ToolResult.to_payload()` shape.)*
- [ ] `run_panel_med_safety()` is registered. Implementation: `get_my_patient_list` → parallel meds + recent labs (renal/hepatic markers) per pid → returns scan envelope. Per-pid gate enforced.
- [ ] `run_cross_cover_onboarding(patient_id)` is registered. Implementation: wider-history fan-out (problems + meds + recent encounters + active orders + hospital-course notes). Gate enforced per nested call.
- [ ] `run_consult_orientation(patient_id, domain)` is registered. `domain` is a constrained string (e.g., `cardiology`, `nephrology`, `id`); the composite filters its fan-out to resources relevant to the domain. Gate enforced per nested call.
- [ ] `run_recent_changes(patient_id, since)` is registered. `since` is an ISO timestamp; composite returns a diff envelope of resources updated/created since that time. Gate enforced per nested call.
- [ ] `run_abx_stewardship(patient_id)` is registered. Implementation: active meds (filtered to antibiotics) + medication administrations + relevant cultures (`DiagnosticReport`/`Observation`) + recent orders. Gate enforced per nested call.
- [~] Synthesis prompts are authored and registered for: W-1 (panel triage / "who do I need to see first?"), W-4 (cross-cover onboarding), W-5 (family-meeting prep — same data shape as W-4 reused via `run_cross_cover_onboarding` plus a different prompt), W-8 (consult orientation), W-9 (re-consult / what changed), W-10 (panel med safety), W-11 (antibiotic stewardship). *(W-1 framing landed alongside `run_panel_triage`; remaining six prompts pair with their composite as each lands.)*
- [~] The synthesis-prompt selector from `issues/006-per-patient-brief-composite.md` is extended to dispatch on all eleven workflow ids; W-6 and W-7 fall through to the default synthesis prompt. *(W-1 added; W-4, W-5, W-8, W-9, W-10, W-11 still unmapped — selector default fall-through still applies.)*
- [~] Tool descriptions guide the LLM clearly: "use `run_panel_triage` when the user asks about prioritization across the panel," "use `run_abx_stewardship` for antibiotic-specific questions," etc. *(`run_panel_triage` description done; remaining composites pending.)*
- [x] Panel-level composites (`run_panel_triage`, `run_panel_med_safety`) only operate over patients returned by `list_panel(user_id)`. Their nested per-pid calls are intrinsically CareTeam-bounded. *(Done for `run_panel_triage`; `run_panel_med_safety` still pending but the pattern is established and unit-tested.)*
- [ ] Eval cases added per workflow: at least one golden conversation per W-1, W-4, W-5, W-8, W-9, W-10, W-11. Per the PRD's *Testing Decisions*, the composite tools themselves are not unit-tested for synthesis quality; that is what the eval harness exists for. The gate enforcement and parallel fan-out behavior, however, are unit-tested. *(Eval-harness drift inherited from issue 003 still unaddressed — adding new W-1 cases on top of a drifting harness would compound the problem; deferred to a single recalibration pass alongside the remaining composites.)*

## Progress notes

### 2026-05-02 — `run_panel_triage` + W-1 synthesis framing landed

Tracer-bullet slice for the panel-level composite pattern. The implementation
mirrors `run_per_patient_brief` (issue 006) but adds an outer-pid fan-out:
`gate.list_panel(user_id)` returns the user's CareTeam roster, then
`asyncio.gather` runs every patient's three-branch sub-fan-out concurrently
(`get_change_signal` + `get_patient_demographics` + `get_active_problems`).
The merged envelope is the granular `ToolResult.to_payload()` shape so
verifier and citation cards stay unchanged.

Key decisions:

- **Roster source: `gate.list_panel(user_id)` rather than `get_my_patient_list`.**
  Both call the same FHIR CareTeam search in real mode, but `list_panel` is
  the gate's own roster (one source of truth shared with the empty-state UI)
  and surfaces typed `ResolvedPatient` rows instead of `Row` envelopes. The
  AC's "Panel-level composites only operate over patients returned by
  `list_panel(user_id)`" reads cleanly when the implementation literally
  calls that method.
- **Empty panel returns ok-empty, not an error.** A user with no team
  assignments is a real day-one state, not a denial. The LLM gets `ok: True`
  with `rows: []` and can say "no patients on your team yet" without
  refusing. Distinguishes from `careteam_denied` which an LLM might mishandle
  as a refusal narrative.
- **Defense-in-depth gate at every per-pid call.** `list_panel` is
  intrinsically CareTeam-bounded, but a buggy refactor that widened it
  (e.g., merged in admin overrides incorrectly) would still be caught at
  every per-pid call. The auth-class denial bubble-up code path mirrors
  `run_per_patient_brief` exactly.
- **Outer fan-out in parallel; inner branches in parallel.** Two-level
  `asyncio.gather`. Wall-clock floor is dominated by `get_change_signal`'s
  own 4-channel serial loop (~200ms with 50ms-per-call jitter on the
  fixture set), but the test deliberately catches a regression that would
  serialize the outer gather (which would 3x the wall clock on
  dr_smith's panel).
- **W-1 synthesis framing added; selector dispatches W-1.** Framing tells
  the LLM to lead with a ranked list, give a one-line per patient, cite
  every claim, and close with a stable-patients summary so nothing's
  hidden by the ranking. Selector regression tests confirm W-1 framing
  doesn't bleed into W-2/W-3/W-7/unclear (and vice-versa).

Files changed:

- `agent/src/copilot/tools.py` — `run_panel_triage` closure +
  `StructuredTool` registration
- `agent/src/copilot/prompts.py` — `_W1_SYNTHESIS_FRAMING` block,
  selector map extended
- `agent/tests/test_panel_triage.py` (new, 9 cases) — envelope shape,
  3-resource per-pid fan-out, panel-bounded scoping (non-admin sees
  only own roster), admin bypass exposes full panel, empty-panel
  ok-empty, parallel fan-out wall-clock, gate enforcement per nested
  call, registration / arg shape, description signals triage intent
- `agent/tests/test_synthesis_prompt_selector.py` — 3 new cases (W-1
  framing markers, mutual exclusion W-1↔W-2, mutual exclusion
  W-1↔W-3); existing W-7 / unclear-fallthrough cases extended to
  also exclude W-1

Tests: 194 backend unit tests pass (was 182; +9 panel triage + 3
selector cases) excluding the Postgres-required files which need a DB
on the sandbox; ruff clean on changed files.

Remaining for this issue (next iterations):

1. `run_panel_med_safety` (W-10 framing) — same outer-pid fan-out as
   `run_panel_triage`, but the inner branches are
   `get_active_medications` + `get_recent_labs` (renal/hepatic
   markers). Existing eval case at `agent/evals/golden/w10_med_safety/`
   pre-dates the composite — picks it up automatically once registered.
2. `run_cross_cover_onboarding` + W-4 framing (and W-5 framing reusing
   the same composite — different framing only).
3. `run_consult_orientation` + W-8 framing — `domain` is a constrained
   string; this is the trickiest composite because the fan-out shape
   varies by domain. Likely uses an enum for `domain` and a per-domain
   resource map.
4. `run_recent_changes` + W-9 framing — `since` ISO timestamp arg; the
   composite is essentially a multi-resource time-window filter.
5. `run_abx_stewardship` + W-11 framing — narrowest of the six;
   filters by antibiotic SNOMED/RxNorm code on top of `meds` + `MARs`
   + cultures.
6. Eval-harness recalibration alongside #1-5. Deferred deliberately:
   adding W-1 / W-10 cases on top of the existing 14-failure drift
   from issue 003 would compound the problem. The single recalibration
   pass is best paired with the composite tool registrations so the
   harness exercises the real production tool surface in one shot.

## Blocked by

- Blocked by `issues/006-per-patient-brief-composite.md` *(unblocked — issue 006 done)*

## User stories addressed

Reference by number from the parent PRD:

- User story 9
- User story 10
- User story 11
- User story 12
- User story 13
- User story 14
