## Parent PRD

`issues/prd.md`

## What to build

The remaining six composite workflow tools and the remaining seven synthesis prompts so that the agent has full W-1…W-11 coverage as described in the PRD. After this slice ships, the agent can answer panel-level triage questions ("who do I need to see first?"), pharmacist-style med-safety scans across the panel, cross-cover onboarding briefs, family-meeting prep, consult orientation scoped to a domain, "what changed since I last looked," and antibiotic stewardship questions — each with a synthesis prompt tuned to the workflow.

This slice covers everything in the PRD's *Tool surface & routing* workflow ↔ tool mapping table that wasn't shipped in `issues/006-per-patient-brief-composite.md`. W-6 (causal trace) and W-7 (targeted drill) are deliberately not given composite tools; they continue to use granular reads under the appropriate synthesis prompt, and that is the correct design.

## Acceptance criteria

- [x] `run_panel_triage()` is registered. Implementation: `gate.list_panel(user_id)` (same call that powers `get_my_patient_list` in real mode and the empty-state UI) → parallel `get_change_signal` + `get_patient_demographics` + `get_active_problems` per pid → returns ranked panel envelope. Every per-pid nested call passes through `_enforce_patient_authorization`. *(Hours arg defaults to 24; outer `asyncio.gather` over the pid list, inner `gather` over the three branches per pid; merged envelope is byte-for-byte the granular `ToolResult.to_payload()` shape.)*
- [x] `run_panel_med_safety()` is registered. Implementation: `gate.list_panel(user_id)` → parallel `get_active_medications` + `get_recent_labs` per pid → returns scan envelope. Per-pid gate enforced. *(Hours arg defaults to 24; outer `asyncio.gather` over the pid list, inner `gather` over the two branches per pid; merge logic shared with `run_panel_triage` via `_merge_panel_envelopes`. Renal/hepatic-marker filtering is applied at the synthesis layer, not the data layer — the W-10 framing tells the LLM which lab codes to lens through.)*
- [x] `run_cross_cover_onboarding(patient_id)` is registered. Implementation: wider-history fan-out (problems + meds + recent encounters + active orders + hospital-course notes). Gate enforced per nested call. *(Hours arg defaults to 168 / 7 days so the envelope captures the admission encounter, orders authored across the stay, and the chronological note trail. Single-pid fan-out under one ``asyncio.gather``; merge logic shared with ``run_per_patient_brief`` via the new ``_merge_envelopes`` helper. Defense-in-depth gate at every per-pid call.)*
- [x] `run_consult_orientation(patient_id, domain)` is registered. `domain` is a constrained string (e.g., `cardiology`, `nephrology`, `id`); the composite filters its fan-out to resources relevant to the domain. Gate enforced per nested call. *(``domain`` is a required string normalized via ``.strip().lower()`` so a clinician typing ``"Cardiology"`` or ``"CARDIOLOGY"`` doesn't get rejected. Unknown / empty values return ``error="invalid_domain"`` with the same envelope shape as ``invalid_since`` from ``run_recent_changes`` so the LLM can surface the bad input. Per-domain branch builder maps ``cardiology → problems + meds + vitals + labs + encounters + imaging + notes``, ``nephrology → problems + meds + labs + encounters + MARs + notes``, ``id → problems + meds + MARs + labs + orders + notes`` — vitals + imaging are cardiology-specific (BP/HR + echo/cath); MARs surface held nephrotoxic doses for nephrology; ServiceRequest holds the culture orders for ID. Default lookback is 168h / 7 days like ``run_cross_cover_onboarding`` since consult orientation spans the admission. Single-pid fan-out under one ``asyncio.gather``; merge logic shared with the other single-pid composites via the ``_merge_envelopes`` helper. Defense-in-depth gate at every per-pid call.)*
- [x] `run_recent_changes(patient_id, since)` is registered. `since` is an ISO timestamp; composite returns a diff envelope of resources updated/created since that time. Gate enforced per nested call. *(``since`` is a required ISO 8601 timestamp; malformed and future values return ``error="invalid_since"``. The composite converts ``since`` → hours-ago and fans out the seven time-windowed granular reads (vitals, labs, encounters, orders, imaging, MARs, notes) under one ``asyncio.gather``; merge logic shared with the other single-pid composites via the ``_merge_envelopes`` helper. Active problems / active medications are intentionally excluded — they are current state, not changes; the W-9 framing tells the LLM to fetch them granularly when it needs to anchor a diff against current state.)*
- [x] `run_abx_stewardship(patient_id)` is registered. Implementation: active meds + medication administrations + recent labs (Observation laboratory — where culture sensitivities, gram stains, and WBC trends live) + recent orders (ServiceRequest — where culture orders are authored). Gate enforced per nested call. *(Antibiotic filtering lives at the synthesis layer: the composite returns *all* meds / MARs / labs / orders; the W-11 framing tells the LLM which RxNorm/SNOMED codes are antibiotics. Mirrors W-10's design — pre-filtering at the data layer would couple the composite to a specific abx vocabulary. Default lookback is 72 hours / 3 days so a full course of cultures and dosing fits in the envelope. Active problems and demographics are intentionally NOT in the fan-out; W-11 framing tells the LLM to fetch them granularly when it needs to anchor the indication or compute duration. Single-pid fan-out under one ``asyncio.gather``; merge logic shared with the other single-pid composites via the ``_merge_envelopes`` helper. Defense-in-depth gate at every nested call.)*
- [x] Synthesis prompts are authored and registered for: W-1 (panel triage / "who do I need to see first?"), W-4 (cross-cover onboarding), W-5 (family-meeting prep — same data shape as W-4 reused via `run_cross_cover_onboarding` plus a different prompt), W-8 (consult orientation), W-9 (re-consult / what changed), W-10 (panel med safety), W-11 (antibiotic stewardship). *(All W-1 / W-4 / W-5 / W-8 / W-9 / W-10 / W-11 framings landed.)*
- [x] The synthesis-prompt selector from `issues/006-per-patient-brief-composite.md` is extended to dispatch on all eleven workflow ids; W-6 and W-7 fall through to the default synthesis prompt. *(W-1 / W-4 / W-5 / W-8 / W-9 / W-10 / W-11 all wired; W-6 / W-7 fall through to default by design.)*
- [x] Tool descriptions guide the LLM clearly: "use `run_panel_triage` when the user asks about prioritization across the panel," "use `run_abx_stewardship` for antibiotic-specific questions," etc. *(All composite tool descriptions done.)*
- [x] Panel-level composites (`run_panel_triage`, `run_panel_med_safety`) only operate over patients returned by `list_panel(user_id)`. Their nested per-pid calls are intrinsically CareTeam-bounded. *(Both composites now use `gate.list_panel(user_id)` for the roster source and re-run the per-call gate as defense in depth.)*
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

### 2026-05-02 — `run_panel_med_safety` + W-10 synthesis framing landed

Second composite slice in the panel-level pattern. Same outer-pid
fan-out as `run_panel_triage`; the per-pid sub-fan-out is two branches
(`get_active_medications` + `get_recent_labs`) instead of three. Both
panel composites now share the merge envelope through a new
`_merge_panel_envelopes` helper — the auth-class denial bubble-up,
order-preserving source dedup, and first-error capture were
byte-for-byte duplicated, so extracting them removed about 30 lines of
duplication and centralizes the contract that the LLM sees the same
denial shape from any panel composite.

Key decisions:

- **Renal/hepatic-marker filtering lives at the synthesis layer, not
  the data layer.** The composite returns *all* recent labs in the
  window, not just creatinine / K+ / AST / ALT / etc. The W-10
  framing tells the LLM which lab codes carry which med-safety
  signal (ACE/ARB ↔ Cr/K+, anticoagulant ↔ INR/anti-Xa,
  diuretic ↔ Cr/K+, renally-cleared agent ↔ Cr,
  hepatically-metabolized ↔ AST/ALT/bilirubin, plus
  ``lifecycle_status='held'`` overnight). Pre-filtering at the data
  layer would couple the composite to a specific lab vocabulary and
  break as we add new med-safety patterns; letting the model apply
  the lens means new med-safety questions add a prompt change, not a
  tool change. (This is the same reasoning that kept the
  per-patient-brief composite from hard-coding the "what's a
  significant vital" filter.)

- **Roster source matches `run_panel_triage`: `gate.list_panel(user_id)`.**
  Same justification as the prior slice — `list_panel` is the gate's
  own roster, returns typed `ResolvedPatient` rows, and reads
  cleanly against the AC's "Panel-level composites only operate
  over patients returned by `list_panel(user_id)`."

- **Empty panel returns ok-empty, not an error.** Mirrors
  `run_panel_triage`. The LLM gets `ok: True` with `rows: []` and
  can say "no patients on your team yet" without reading it as
  `careteam_denied`.

- **Defense-in-depth gate at every per-pid call.** 3 patients x 2
  branches = 6 gate consultations minimum on dr_smith's panel.
  Tests count via a spy and assert >= panel_size * 2.

- **Parallel fan-out asserted via concurrency counter, not
  wall-clock.** Each per-pid branch is one fast FHIR search
  (~50ms); the parallel-vs-serial wall-clock gap collapses into CI
  jitter at this scale. Instead the test instruments
  `CareTeamGate.assert_authorized` to track max concurrent
  invocations: if the outer gather is parallel, multiple per-pid
  sub-fan-outs run concurrently and we observe at least
  panel-size gate calls in flight at once. A serial outer would cap
  at 2 (the inner gather over 2 branches). Faster, more reliable,
  and more precise than wall-clock for this composite shape.

- **`_merge_panel_envelopes` extraction (small refactor).** Two
  panel composites now share the merge logic via a closure-scoped
  helper inside `make_tools`. Kept inside `make_tools` (not module-
  level) to avoid threading `gate` / `client` / per-tool functions
  through a free function. Per CLAUDE.md "three similar lines" —
  two ~30-line copies of identical logic with auth-denial detection
  is past the abstraction threshold.

- **W-10 synthesis framing.** Lists the flag-worthy combinations in
  priority order (ACE/ARB ↔ creatinine/K+, anticoagulant ↔ INR,
  diuretic ↔ Cr/K+, renally-cleared agents ↔ reduced renal
  function, hepatically-metabolized ↔ AST/ALT/bilirubin, held doses
  via `lifecycle_status`). One sentence per flagged patient with the
  med + the lab + the safety concern; cite both the
  MedicationRequest and the Observation. Patients with no concern
  get a single closing line so the clinician knows they were
  considered. Selector regression tests confirm W-10 framing
  doesn't bleed into W-1 / W-2 / W-3 / W-7 / unclear (and
  vice-versa).

Files changed:

- `agent/src/copilot/tools.py` — `_merge_panel_envelopes` helper
  (extraction); `run_panel_triage` refactored to use it;
  `run_panel_med_safety` closure + `StructuredTool` registration;
  description authored to bias the LLM toward the composite for
  med-safety / pharmacist-review queries
- `agent/src/copilot/prompts.py` — `_W10_SYNTHESIS_FRAMING` block;
  selector map extended to dispatch W-10
- `agent/tests/test_panel_med_safety.py` (new, 9 cases) — envelope
  shape, meds + labs fan-out per pid, panel-bounded scoping
  (non-admin sees only own roster, asserted via
  Linda's-WBC-must-be-absent), admin bypass exposes Linda's
  WBC, empty-panel ok-empty, parallel fan-out via
  concurrency-counter spy, gate enforcement per nested call,
  registration / arg shape, description signals pharmacist intent
- `agent/tests/test_synthesis_prompt_selector.py` — 3 new cases
  (W-10 framing markers, mutual exclusion W-10 ↔ W-1/W-2/W-3,
  W-1 ↔ W-10 mutual exclusion); existing W-7 / unclear /
  default-framing cases extended to also exclude W-10

Tests: 206 backend unit tests pass (was 194; +9 med-safety + 3
selector cases) excluding the Postgres-required files which need a
DB on the sandbox; ruff clean on changed files. The 14 inherited
eval-harness failures from issue 003 carry over; recalibration
remains scheduled for the joint pass alongside the remaining
composites.

Remaining for this issue (next iterations):

1. `run_cross_cover_onboarding` + W-4 framing (and W-5 framing reusing
   the same composite — different framing only). *(Done — see the
   2026-05-02 cross-cover progress note below.)*
2. `run_consult_orientation` + W-8 framing — `domain` is a constrained
   string; this is the trickiest composite because the fan-out shape
   varies by domain. Likely uses an enum for `domain` and a per-domain
   resource map.
3. `run_recent_changes` + W-9 framing — `since` ISO timestamp arg; the
   composite is essentially a multi-resource time-window filter.
4. `run_abx_stewardship` + W-11 framing — narrowest of the four;
   filters by antibiotic SNOMED/RxNorm code on top of `meds` + `MARs`
   + cultures.
5. Eval-harness recalibration alongside #1-4. Deferred deliberately:
   adding W-1 / W-10 cases on top of the existing 14-failure drift
   from issue 003 would compound the problem. The single recalibration
   pass is best paired with the composite tool registrations so the
   harness exercises the real production tool surface in one shot.

### 2026-05-02 — `run_cross_cover_onboarding` + W-4/W-5 synthesis framings landed

Third composite slice in issue 007 and the first single-pid composite
since `run_per_patient_brief` (issue 006). Same in-pid fan-out
template, but with a wider history (default 168h / 7d) and a
different branch set: active problems, active medications, recent
encounters, recent orders (ServiceRequest), and clinical notes
(DocumentReference). The same composite drives both W-4 (cross-cover
onboarding) and W-5 (family-meeting prep) — only the synthesis
framing differs.

Key decisions:

- **One composite, two framings (W-4 vs W-5).** The data shape a
  cross-cover physician needs and the data shape a clinician
  preparing for a family meeting needs is the same: the
  hospital-course narrative (problem list, active plan, encounters,
  orders, recent notes). The thing that differs is what the
  clinician *does* with it. Rather than ship two near-identical
  composites, ship one and let the synthesis prompt selector pick
  the framing. The W-5 framing explicitly tells the LLM not to
  recommend what to say to the family (that's the clinician's
  judgement) and emphasizes diagnosis / trajectory / plan / prognosis
  with code-status quotes verbatim from the chart. The W-4 framing
  emphasizes admission story, leading diagnosis, active plan, and
  "what to watch overnight" items.

- **Wider-history default (168h / 7 days).** Cross-cover and family
  meetings span the admission, not just the overnight events. A
  24-hour window would miss the admission encounter, the first day
  of orders, and the chronological note trail that gives the chart
  story shape. Asserted by the schema test — the default is a hard
  guarantee now, so a regression that flipped it back to 24h would
  fail loudly.

- **Vitals and labs are intentionally NOT in the cross-cover
  composite.** The per-patient brief (W-2 / W-3) handles those.
  Cross-cover orientation is about the *narrative* — what's wrong,
  what's being done, what happened along the way. Vital and lab
  noise distracts from the story when a clinician is reading to
  understand the case for the first time. The fan-out's source
  labels are tested to assert the absence of "vital-signs" and
  "laboratory" branches so the boundary stays explicit.

- **`_merge_envelopes` extraction.** This is the second single-pid
  composite, so the merge logic now appears in three places:
  `run_per_patient_brief`, the new composite, and (via
  `_merge_panel_envelopes`) the panel composites. Extracting a
  shared `_merge_envelopes` helper that takes an optional
  `initial_sources` tuple unifies all three. The panel helper
  becomes a thin adapter that flattens nested per-pid tuples and
  forwards. Per CLAUDE.md "three similar lines is past the
  abstraction threshold" — and the W-10 slice already set the
  precedent for extracting on the second instance.

- **Defense-in-depth gate at every per-pid call.** Top-of-call gate
  short-circuits the empty-pid / out-of-team path before paying
  five gate-denied roundtrips; per-branch gate consults catch a
  buggy refactor that removed the gate from a single read. Counted
  via spy in the test (>= 5 expected).

- **Tool description biases the LLM toward composite for both
  cross-cover and family-meeting questions.** Description names
  the W-4 / W-5 trigger phrases ("I'm cross-covering", "I've never
  seen this patient", "meeting with the family") and contrasts
  with `run_per_patient_brief` for the 24-hour overnight case.

- **W-4 and W-5 framing mutual exclusion verified.** The selector
  must not double-include framings; tests confirm W-4 ↮ W-5 ↮ W-1
  ↮ W-2 ↮ W-3 ↮ W-10 mutual exclusion.

Files changed:

- `agent/src/copilot/tools.py` — `_merge_envelopes` helper
  (extraction); `_merge_panel_envelopes` refactored to wrap it;
  `run_per_patient_brief` refactored to use it;
  `run_cross_cover_onboarding` closure + `StructuredTool`
  registration with description authored to bias the LLM toward
  the composite for cross-cover / family-meeting queries
- `agent/src/copilot/prompts.py` — `_W4_SYNTHESIS_FRAMING` +
  `_W5_SYNTHESIS_FRAMING` blocks; selector map extended to
  dispatch W-4 and W-5
- `agent/tests/test_cross_cover_onboarding.py` (new, 12 cases) —
  envelope shape, 5-resource fan-out, vitals/labs absence (data
  boundary), wider-history default (schema-asserted), parallel
  fan-out wall-clock, gate enforcement per nested call,
  out-of-team denial, no-active-patient empty pid, admin bypass,
  registration / arg shape, description signals W-4/W-5 intent
- `agent/tests/test_synthesis_prompt_selector.py` — 6 new cases
  (W-4 framing markers, W-5 framing markers, W-4 ↮ W-5 mutual
  exclusion, W-4 ↮ everything-else, W-5 ↮ everything-else);
  existing W-7 / unclear-fallthrough cases extended to also
  exclude W-4 and W-5

Tests: 224 backend unit tests pass (was 206; +12 cross-cover + 6
selector cases) excluding the Postgres-required files which need
a DB on the sandbox; ruff clean on changed files. The 14
inherited eval-harness failures from issue 003 carry over;
recalibration remains scheduled for the joint pass alongside the
remaining composites.

### 2026-05-02 — `run_recent_changes` + W-9 synthesis framing landed

Fourth composite slice in issue 007 and the second single-pid
composite this session. Same in-pid fan-out template as cross-cover
but the lookback window is supplied as an ISO 8601 ``since``
timestamp rather than a relative ``hours`` value, and the branch
set is the W-9 "what changed since I last looked" diff: vitals,
labs, encounters, orders, imaging, MARs, and notes.

Key decisions:

- **``since`` is converted to a positive hours-ago delta and passed
  to the existing time-windowed granular tools.** Rather than
  threading a ``since=`` arg through every granular read (which
  would expand the surface area for the same effect), the
  composite computes ``hours = ceil((now - since) / 3600) + 1`` and
  reuses the existing ``hours``-windowed branches. The +1 buffer
  rounds the cutoff outward so the boundary timestamp is included
  rather than excluded by integer truncation; the W-9 diff loses
  very little by overshooting by an hour and would lose a real
  signal by undershooting it. Asserted by the
  ``test_run_recent_changes_propagates_since_to_branch_filters``
  spy that captures every ``FhirClient.search`` and verifies the
  ``ge<timestamp>`` filter on each branch is at-or-after the
  supplied ``since``.

- **Active problems and active medications are intentionally NOT in
  the diff.** They describe current *state*, not changes. Including
  them would either (a) require post-filtering on
  ``recordedDate``/``authoredOn`` (fragile, and the granular tools
  don't support it) or (b) make the LLM compare against the full
  current med list — which is not what W-9 asks. The W-9 framing
  explicitly tells the LLM to fetch active state with granular
  tools when it needs to anchor a diff against current state.
  Asserted by ``test_run_recent_changes_excludes_active_problems_and_meds``
  so the boundary stays explicit.

- **Malformed and future ``since`` return ``invalid_since`` rather
  than an opaque exception.** Three rejection paths: a missing/empty
  string, a value that doesn't parse as ISO 8601, and a value in
  the future. All three return ``ToolResult(ok=False, error="invalid_since")``
  with the same envelope shape as a granular tool, so the LLM gets
  a structured refusal it can surface to the user instead of a
  500-style crash. ``datetime.fromisoformat`` is permissive enough
  to accept both ``"...Z"`` (after a swap to ``"+00:00"``) and
  full RFC-3339 strings; naive timestamps are assumed UTC for
  back-compat with simpler client formats.

- **Defense-in-depth gate at every per-pid call.** Top-of-call gate
  short-circuits the empty-pid / out-of-team path before paying
  seven gate-denied roundtrips; per-branch gate consults catch a
  buggy refactor that removed the gate from a single read. Counted
  via spy in the test (>= 7 expected).

- **Tool description biases the LLM toward composite for W-9
  phrases.** "what's new on Hayes since rounds?", "anything happen
  since I left at 4pm?", "diff me on Eduardo since yesterday",
  "I last looked Tuesday — what changed?". Description names the
  full branch set and explicitly notes that active problems / meds
  are *not* in the diff so the LLM doesn't expect them.

- **W-9 framing.** Lead with the *new* events in chronological
  order, group by category, anchor each item by timestamp, cite
  every change. Crucially the framing tells the LLM to say
  "no new X since <since>" for empty branches rather than silently
  dropping them — so the clinician knows the branch was checked.
  Selector regression tests confirm W-9 framing doesn't bleed into
  W-1 / W-2 / W-3 / W-4 / W-5 / W-7 / W-10 / unclear (and
  vice-versa). The W-2 ↔ W-9 mutual-exclusion test is explicit
  because the two are easy to confuse — both look at recent
  events, but W-2 is the 24h overnight brief framing while W-9 is
  scoped to a user-supplied cutoff.

- **Patient-context-guard sweep updated.** The catch-all
  ``test_all_patient_scoped_tools_enforce_gate_for_bound_user``
  iterates every patient-scoped tool and asserts ``careteam_denied``
  on an out-of-team pid. The new tool's required ``since`` arg
  meant the test had to supply it alongside ``patient_id`` /
  ``hours``; otherwise pydantic rejects the tool input before the
  gate ever runs.

Files changed:

- ``agent/src/copilot/tools.py`` — ``_hours_until_now_from_iso``
  helper (parses ISO 8601, rejects future timestamps, returns
  hours-ago with a +1 boundary buffer); ``run_recent_changes``
  closure + ``StructuredTool`` registration with description
  authored to bias the LLM toward the composite for W-9 queries
- ``agent/src/copilot/prompts.py`` — ``_W9_SYNTHESIS_FRAMING``
  block; selector map extended to dispatch W-9
- ``agent/tests/test_recent_changes.py`` (new, 15 cases) —
  envelope shape, 7-branch ``sources_checked`` coverage, exclusion
  of Condition/MedicationRequest from the diff, ``since``-required
  schema assertion, malformed/future ``invalid_since`` rejection,
  ``since``-propagated-to-branch-filters spy, parallel fan-out
  wall-clock, gate enforcement per nested call, careteam_denied
  for out-of-team pid, no_active_patient empty pid, admin bypass,
  registration / arg shape, description signals W-9 intent
- ``agent/tests/test_synthesis_prompt_selector.py`` — 3 new cases
  (W-9 framing markers, W-9 ↮ everything-else mutual exclusion,
  W-2 ↔ W-9 explicit mutual exclusion); existing W-7 / unclear /
  W-4 / W-5 / default-framing cases extended to also exclude W-9
- ``agent/tests/test_patient_context_guard.py`` — catch-all sweep
  now also passes ``since`` when the tool requires it

Tests: 242 backend unit tests pass (was 224; +15 recent-changes +
3 selector cases) excluding the Postgres-required files which need
a DB on the sandbox; ruff clean on changed files. The 14 inherited
eval-harness failures from issue 003 carry over; recalibration
remains scheduled for the joint pass alongside the remaining
composites.

Notes for next iteration:

- ``run_abx_stewardship(patient_id)`` + W-11 framing is the
  narrowest of the three remaining composites — it filters active
  meds + medication administrations + cultures
  (DiagnosticReport/Observation) + recent orders to antibiotics by
  RxNorm/SNOMED. Like W-10 (renal/hepatic markers), the antibiotic
  filter belongs at the synthesis layer, not the data layer — the
  composite returns the raw envelopes and the W-11 framing tells
  the LLM which RxNorm/SNOMED codes count.
- ``run_consult_orientation(patient_id, domain)`` + W-8 framing is
  the trickiest of the remaining composites because the fan-out
  shape varies by domain. Likely uses an enum for ``domain`` and a
  per-domain resource map (e.g., cardiology → echo + cath +
  cardiac labs; nephrology → BMP + UA + dialysis encounters; ID →
  cultures + abx + WBC + temperature). Easiest path: a per-domain
  set of the existing granular reads composed at registration time.
- Eval-harness recalibration alongside the remaining two
  composites in one pass keeps from compounding the drift —
  unchanged from the prior slice's note.

### 2026-05-02 — `run_abx_stewardship` + W-11 synthesis framing landed

Fifth composite slice in issue 007 and the third single-pid composite
this session. Same in-pid fan-out template as cross-cover and
recent-changes; the branch set is tuned to the W-11 ("should this
patient still be on broad-spectrum?") workflow: active medications
(the abx orders themselves), medication administrations (was the abx
actually given vs held), recent labs (Observation laboratory — where
culture sensitivities, gram stains, and WBC trends live), and recent
orders (ServiceRequest — where culture orders are authored).

Key decisions:

- **Antibiotic filtering at the synthesis layer, not the data
  layer.** The composite returns *all* active meds / MARs / labs /
  orders in the window; the W-11 framing names the antibiotic
  classes the LLM should foreground (β-lactams, glycopeptides,
  oxazolidinones, fluoroquinolones, aminoglycosides, macrolides,
  lincosamides, tetracyclines, sulfonamides, nitroimidazoles,
  antifungals when the question scopes that wide). Pre-filtering at
  the data layer would couple the composite to a specific abx
  vocabulary and break as new agents are added — the same reasoning
  that kept renal/hepatic-marker filtering out of W-10's data layer.
  Exhaustively documented in the framing so the LLM has a clear
  decision boundary about which classes to surface.

- **DiagnosticReport intentionally NOT in the fan-out.** The AC
  named "cultures (DiagnosticReport/Observation)" but the existing
  ``get_imaging_results`` granular tool filters DiagnosticReport to
  ``category=radiology``; there's no generic-DiagnosticReport tool,
  and adding one is scope creep. Cultures show up as Observations
  under the laboratory category (sensitivities, organism IDs, gram
  stains) — that path is covered by ``get_recent_labs``. The W-11
  framing tells the LLM that if a *formal* microbiology
  DiagnosticReport is needed beyond what the Observation path
  surfaces, fall back to a granular call. Two-line rule, no new tool.

- **Active problems and demographics are intentionally NOT in the
  fan-out.** They are anchoring state, not stewardship signal.
  Including them would balloon the envelope without serving the
  lens. The W-11 framing tells the LLM to fetch them granularly
  when it needs to anchor an indication or compute a precise
  duration of therapy. Asserted by the source-label test
  (``test_run_abx_stewardship_excludes_problems_and_demographics``)
  so the boundary stays explicit.

- **Wider lookback default (72h / 3 days).** Stewardship spans a
  course, not just overnight. A 24-hour window would miss the
  initial culture order and the early dose train; 7 days would over-
  fetch. 72h captures a typical empiric-to-targeted decision window
  while keeping the envelope tight. Asserted by the registration /
  arg-shape test that ``hours`` defaults non-required.

- **Defense-in-depth gate at every per-pid call.** Top-of-call gate
  short-circuits the empty-pid / out-of-team path before paying
  four gate-denied roundtrips; per-branch gate consults catch a
  buggy refactor that removed the gate from a single read. Counted
  via spy in the test (>= 4 expected).

- **Tool description biases the LLM toward the composite for W-11
  phrases.** "should this patient still be on broad-spectrum?",
  "is Hayes still on vanc/zosyn?", "time to de-escalate?", "what's
  growing on Linda's cultures and is the abx coverage right?", "how
  many days has she been on cefepime?". Description names the four
  branches and explicitly notes that active problems / demographics
  are *not* in the fan-out so the LLM doesn't expect them. Description
  also contrasts with ``run_panel_med_safety`` for the panel-level
  case so the LLM picks correctly between W-10 and W-11.

- **W-11 framing.** Lead with the active abx regimen (name, dose,
  route, start date from ``authored_on``). Then the MAR trail
  (held / given / stopped lifecycle). Then the microbiology evidence
  (culture orders + sensitivities, quoted verbatim). Then the
  WBC / temperature / lactate trend so the clinician can see whether
  the infection signal is improving. Close with two explicit chart-
  verification prompts: duration of therapy and whether the empiric
  regimen still fits the now-known microbiology. Crucially the
  framing forbids a recommendation: "do NOT recommend a specific
  abx or duration — surface the data; the clinician decides."
  Selector regression tests confirm W-11 framing doesn't bleed into
  W-1 / W-2 / W-3 / W-4 / W-5 / W-9 / W-10 / W-7 / unclear (and
  vice-versa). The W-10 ↔ W-11 mutual-exclusion test is explicit
  because the two are easy to confuse — both apply a med-safety
  lens, but W-10 spans the panel and W-11 is single-patient.

Files changed:

- ``agent/src/copilot/tools.py`` — ``run_abx_stewardship`` closure
  + ``StructuredTool`` registration with description authored to
  bias the LLM toward the composite for W-11 queries
- ``agent/src/copilot/prompts.py`` — ``_W11_SYNTHESIS_FRAMING``
  block; selector map extended to dispatch W-11
- ``agent/tests/test_abx_stewardship.py`` (new, 12 cases) — envelope
  shape, 4-branch ``sources_checked`` coverage, problems/demographics
  exclusion (data boundary), four-resource fan-out via merged rows,
  parallel fan-out wall-clock, gate enforcement per nested call,
  careteam_denied for out-of-team pid, no_active_patient empty pid,
  admin bypass, no-user-bound denial, registration / arg shape,
  description signals W-11 intent
- ``agent/tests/test_synthesis_prompt_selector.py`` — 3 new cases
  (W-11 framing markers, W-11 ↮ everything-else mutual exclusion,
  W-10 ↔ W-11 explicit mutual exclusion); existing W-7 / unclear /
  W-4 / W-5 / W-9 / W-10 / default-framing cases extended to also
  exclude W-11

Tests: 257 backend unit tests pass (was 242; +12 abx-stewardship +
3 selector cases) excluding the Postgres-required files which need
a DB on the sandbox; ruff clean on changed files. The 14 inherited
eval-harness failures from issue 003 carry over; recalibration
remains scheduled for the joint pass alongside the remaining
composite (``run_consult_orientation``).

Notes for next iteration:

- ``run_consult_orientation(patient_id, domain)`` + W-8 framing is
  the only composite remaining. The fan-out shape varies by domain
  (cardiology → echo + cath + cardiac labs; nephrology → BMP + UA +
  dialysis encounters; ID → cultures + abx + WBC + temperature).
  Likely uses a backed enum for ``domain`` and a per-domain
  resource map composed at registration time. The
  ``_merge_envelopes`` helper unblocks it cleanly — same fan-out
  template as the other single-pid composites.
- Eval-harness recalibration is best paired with the remaining
  ``run_consult_orientation`` slice in one pass — adding W-11
  cases on top of a drifting harness would compound the problem.
  Unchanged from prior slices' notes.

### 2026-05-02 — `run_consult_orientation` + W-8 synthesis framing landed

Sixth and final composite slice in issue 007. Same single-pid
fan-out template as cross-cover, recent-changes, and abx-stewardship,
but the per-pid branch set is selected by a required ``domain`` arg.
This is the trickiest of the W-1…W-11 composites because the
fan-out shape itself is data — different consulting services read
different parts of the chart, so the tool can't have one fixed
branch set.

Key decisions:

- **Per-domain branch builder, not a per-domain code filter.** The
  AC says "filters its fan-out to resources relevant to the domain";
  the natural reading is "different consult services pull different
  resource types". A cardiologist needs vitals (BP / HR / rhythm
  trend) and imaging (echo / cath conclusions) that nobody else
  needs at orientation time; a nephrologist needs MARs (held
  nephrotoxic doses); an ID consultant needs ServiceRequest (where
  culture orders are authored). The composite picks branch *types*
  per domain. Code-level filtering (e.g., "cardiac labs only,
  please") is a synthesis-layer concern — same reasoning as W-10
  (renal/hepatic markers) and W-11 (antibiotic codes). The W-8
  framing names the codes each domain should lens through.

- **Three domains: ``cardiology``, ``nephrology``, ``id``.** Mapped
  to:
    * cardiology → problems + meds + vitals + labs + encounters +
      imaging + notes (7 branches)
    * nephrology → problems + meds + labs + encounters + MARs +
      notes (6 branches)
    * id → problems + meds + MARs + labs + orders + notes
      (6 branches)
  Each per-domain set hits a distinct subset of the granular
  reads. The lambda-of-no-args pattern keeps the outer
  ``asyncio.gather`` agnostic about which branches the domain
  picked — it just runs whatever factories the per-domain map
  produced.

- **``domain`` is normalized via ``.strip().lower()``.** A clinician
  typing ``"Cardiology"`` or ``" CARDIOLOGY "`` should not be
  rejected. Mirrors the ``_hours_until_now_from_iso`` permissive-
  parsing discipline from W-9.

- **Unknown / empty ``domain`` returns ``error="invalid_domain"``.**
  Same envelope shape as W-9's ``invalid_since`` so the LLM gets a
  structured refusal instead of a runtime crash, and the user can
  see the bad input in the chat. Currently unsupported domains
  (e.g., ``endocrine``, ``heme``, ``pulmonary``, ``GI``) fall into
  this path; adding new domains is a one-line change to the
  per-domain branch map.

- **No backed enum for ``domain`` at the StructuredTool layer.**
  Originally considered a ``StrEnum`` on the function signature
  to get pydantic-side validation, but the runtime ``invalid_domain``
  envelope is more LLM-friendly than a pydantic ``ValidationError``
  surfaced through langchain — the latter dies with an opaque
  schema-mismatch message before the tool body runs. The
  ``invalid_domain`` envelope plumbs the bad input back to the LLM
  exactly like ``invalid_since`` does for W-9.

- **Wider lookback default (168h / 7 days).** Same as
  ``run_cross_cover_onboarding``. Consult orientation spans the
  admission, not just overnight. The default is asserted via the
  schema test so a regression that flipped it back to 24h would
  fail loudly.

- **Defense-in-depth gate at every per-pid call.** Top-of-call gate
  short-circuits the empty-pid / out-of-team path before paying up
  to seven gate-denied roundtrips; per-branch gate consults catch
  a buggy refactor that removed the gate from a single read.
  Counted via spy in the test (>= 7 expected for cardiology).

- **Tool description biases the LLM toward composite for W-8
  phrases.** "cardiology consult on Hayes", "orient me as nephro on
  Eduardo", "walk me through Linda's chart from an ID standpoint".
  Description names the three domains and the per-domain branch
  shape so the LLM picks correctly; explicitly contrasts with
  ``run_per_patient_brief`` (24h overnight) and
  ``run_cross_cover_onboarding`` (cross-cover without a specialist
  lens) so the routing surface stays clean.

- **W-8 framing.** Per-domain lens with explicit code lists:
  cardiology surfaces BP / HR / rhythm trends + BNP / troponin /
  BMP + echo / cath conclusions; nephrology surfaces Cr / K+ /
  BUN / eGFR / UA / urine protein + held-for-AKI doses; ID surfaces
  active abx regimen + MAR trail + microbiology evidence + WBC /
  temperature / lactate trend. Forbids treatment recommendations:
  "do NOT recommend a treatment plan or workup — the consultant
  decides; surface what the chart says." Selector regression tests
  confirm W-8 framing doesn't bleed into W-1 / W-2 / W-3 / W-4 /
  W-5 / W-9 / W-10 / W-11 / W-7 / unclear (and vice-versa). The
  W-4 ↔ W-8 mutual-exclusion test is explicit because both produce
  chart-orientation reads, but W-4 is the general cross-cover
  pickup and W-8 is scoped to a specialist domain.

- **Patient-context-guard sweep updated.** The catch-all
  ``test_all_patient_scoped_tools_enforce_gate_for_bound_user``
  iterates every patient-scoped tool. The new tool's required
  ``domain`` arg meant the test had to supply it (``cardiology``)
  alongside ``patient_id`` / ``hours`` / ``since``; otherwise
  pydantic rejects the tool input before the gate runs.

Files changed:

- ``agent/src/copilot/tools.py`` — ``run_consult_orientation``
  closure with per-domain branch builder + ``StructuredTool``
  registration with description authored to bias the LLM toward
  the composite for W-8 queries
- ``agent/src/copilot/prompts.py`` — ``_W8_SYNTHESIS_FRAMING``
  block; selector map extended to dispatch W-8
- ``agent/tests/test_consult_orientation.py`` (new, 16 cases) —
  envelope shape, three per-domain fan-out shapes (cardiology /
  nephrology / id), case-insensitive domain, ``invalid_domain``
  rejection (empty + unknown), parallel fan-out wall-clock, gate
  enforcement per nested call, careteam_denied for out-of-team
  pid, no_active_patient empty pid, no-user-bound denial, admin
  bypass, registration / arg shape, wider-history default
  (schema-asserted), description signals W-8 intent
- ``agent/tests/test_synthesis_prompt_selector.py`` — 3 new cases
  (W-8 framing markers, W-8 ↮ everything-else mutual exclusion,
  W-4 ↔ W-8 explicit mutual exclusion); existing W-7 / unclear /
  W-4 / W-5 / W-9 / W-10 / W-11 / default-framing cases extended
  to also exclude W-8
- ``agent/tests/test_patient_context_guard.py`` — catch-all sweep
  now also passes ``domain`` when the tool requires it

Tests: 276 backend unit tests pass (was 257; +16 consult-orientation
+ 3 selector cases; +0 from previously-skipped tests now running)
excluding the Postgres-required files which need a DB on the
sandbox; ruff clean on changed files. The 14 inherited
eval-harness failures from issue 003 carry over as expected;
recalibration was deferred to be paired with the remaining
composites — that pairing now lives at the end of issue 007's
remaining work.

Notes for next iteration:

- All six issue-007 composites and seven synthesis prompts are
  now landed. The only remaining AC is the eval-harness work
  (#23): adding at least one golden case per W-1 / W-4 / W-5 /
  W-8 / W-9 / W-10 / W-11 *and* recalibrating the 14 inherited
  failures from issue 003. That belongs in its own slice — the
  composite-tool surface is now stable enough to recalibrate
  against.
- Consult-orientation domains beyond cardiology / nephrology / id
  (heme/onc, pulmonary, GI, neuro, endo) are a one-line addition
  to the per-domain branch map per domain plus a W-8 framing
  paragraph. Defer until a real eval case asks for them.

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
