# Restore smoke-002 (active medications)

## Parent PRD

Conversation context: 2026-05-08 smoke-tier regression discovered while
re-running evals. README claimed 83.3% (5/6) pass rate dated 2026-05-06;
re-run with `USE_FIXTURE_FHIR=1` forced now reports 50% (3/6) with three
named regressions. This is the first.

## What to build

`evals/smoke/002_active_meds.yaml` asks "What active medications is this
patient on?" against fixture-1 (Eduardo). The case requires three
substrings (`furosemide`, `lisinopril`, `metoprolol`) and three
`MedicationRequest/med-*` citations, sourced via the granular
`get_active_medications` tool.

The agent currently fails this case after `USE_FIXTURE_FHIR=1` was
forced in `evals/conftest.py` (so this is not the env wiring bug — it
is real behavioral regression).

Diagnose where the chain is breaking — classifier picking wrong
workflow, supervisor/agent not selecting `get_active_medications`,
the granular tool returning the wrong shape, or synthesis dropping
citations — and fix at the right layer. End-to-end behavior must
match what the YAML expects, not "close enough" string-matching
hacks.

## Acceptance criteria

- [ ] `pytest evals/test_smoke.py::test_smoke_case[smoke-002-active-meds] -v` passes locally with `USE_FIXTURE_FHIR=1` (now forced via conftest)
- [ ] Response substrings include `furosemide`, `lisinopril`, `metoprolol` (case-insensitive — see `Case.required_facts` matcher)
- [ ] Citations resolve to `MedicationRequest/med-furosemide`, `MedicationRequest/med-lisinopril`, `MedicationRequest/med-metoprolol`
- [ ] Trajectory includes `get_active_medications` (per the case's `required_tools`)
- [ ] Cost ≤ $0.50 and latency ≤ 30 s per case (existing budget)
- [ ] No new baseline carve-out in `.eval_baseline.json` to suppress the failure — fix at the source
- [ ] If the root cause is shared with `008-restore-smoke-003-overnight-event.md` and/or `009-restore-smoke-004-triage-panel.md`, note that in the PR description so the others can be closed with a reference

## Blocked by

None — can start immediately. Note that
`evals/conftest.py` already forces `USE_FIXTURE_FHIR=1` (committed
separately), so the diagnostic environment is reproducible.

## User stories addressed

The smoke tier is the PR-block gate (per `agent/README.md` "Three
tiers — smoke (every PR)"). Without this case green, the
medication-list path in W-2 has no automated coverage that this
regression can be caught next time it drifts.
