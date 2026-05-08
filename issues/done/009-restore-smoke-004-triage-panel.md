# Restore smoke-004 (triage panel)

## Parent PRD

Conversation context: 2026-05-08 smoke-tier regression discovered while
re-running evals. See sibling `007-restore-smoke-002-active-medications.md`
for broader context. Of the three regressions, this is the most
informative — the agent picks the wrong tool entirely rather than
producing an under-cited answer.

## What to build

`evals/smoke/004_triage_panel.yaml` exercises the W-1 cross-panel
triage path. Expects the agent to call `run_panel_triage` and
surface patient names from `dr_lopez`'s CareTeam roster
(includes "Perez", "Hayes", "Park" among others — sourced from
`fixtures.py` CareTeam resources).

The agent currently:

- Does NOT call `run_panel_triage`
- Synthesizes a numbered "Patient 1..5" list using fabricated
  `Observation/_summary=count?patient=fixture-N` citations that are
  not actual fetched FHIR resources
- Misses every required name substring (`Perez`, `Hayes`, `Park`)

Two layers to investigate:

1. **Tool selection** — why is the supervisor / agent not choosing
   `run_panel_triage` for a panel-level prompt?
2. **Citation hygiene** — the verifier should be rejecting a citation
   like `Observation/_summary=count?patient=fixture-3` (URL-encoded
   query, not a resource id). The fact that it survived to the
   response means the verifier's URL/ref grammar is too lax. Tighten
   it whether or not the tool-selection fix lands.

Both fixes belong in this slice because layer (2) is a real
correctness hole independent of the smoke regression.

## Acceptance criteria

- [ ] `pytest evals/test_smoke.py::test_smoke_case[smoke-004-triage-panel] -v` passes
- [ ] Trajectory includes `run_panel_triage`
- [ ] Response substrings include `Perez`, `Hayes`, `Park` (case-insensitive)
- [ ] Verifier rejects a citation whose ref looks like
      `<ResourceType>/_summary=...` or any other querystring-shaped
      ref instead of a bare resource id (add a unit test in
      `tests/test_verifier_*.py` or equivalent)
- [ ] Cost ≤ $0.50, latency ≤ 30 s
- [ ] No `.eval_baseline.json` carve-out
- [ ] PR description names whether the tool-selection drift is shared
      with `007` / `008`; close them together if so

## Blocked by

None — can start immediately. Conftest fix is already in.

## User stories addressed

UC-1 cross-panel triage (`ARCHITECTURE.md` §UC-1) is the second
demo workflow alongside W-2 brief. Without this case green, the
panel-level path is uncovered, and — separately — the verifier
correctness hole this exposes (lax citation-ref grammar) could
mask other faked citations across all workflows.
