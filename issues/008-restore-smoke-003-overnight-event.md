# Restore smoke-003 (overnight event)

## Parent PRD

Conversation context: 2026-05-08 smoke-tier regression discovered while
re-running evals. See sibling `007-restore-smoke-002-active-medications.md`
for the broader context (3 of 6 smoke cases regressed since
2026-05-06's claimed 83.3%).

## What to build

`evals/smoke/003_overnight_event.yaml` is the canonical UC-2 case from
`ARCHITECTURE.md`. Asks "What happened to this patient overnight?"
against fixture-1 (Eduardo), where the fixture has an explicit
hypotensive cascade (138/82 → 90/60 → 112/70) plus a nursing note
documenting the bolus.

The agent must:

- Surface "90/60" and "bolus" in the response
- Cite `Observation/obs-bp-2` AND `DocumentReference/doc-overnight-note`
- Pick `run_per_patient_brief` (the composite, not chained granular reads)

Currently failing after `USE_FIXTURE_FHIR=1` was forced.

Diagnose where the chain breaks. Likely culprits: the supervisor
prompt no longer steering toward the brief composite, the
composite tool returning a payload that doesn't make the BP cascade
salient to the synthesis prompt, or the verifier stripping a
citation it shouldn't. Fix at the layer where the regression
actually lives.

## Acceptance criteria

- [ ] `pytest evals/test_smoke.py::test_smoke_case[smoke-003-overnight-event] -v` passes
- [ ] Response includes `90/60` and `bolus` (case-insensitive)
- [ ] Citations resolve to `Observation/obs-bp-2` and `DocumentReference/doc-overnight-note`
- [ ] Trajectory includes `run_per_patient_brief`
- [ ] Cost ≤ $0.50, latency ≤ 30 s
- [ ] No `.eval_baseline.json` carve-out
- [ ] If the fix is shared with `007` or `009`, reference them in the PR description and close them together

## Blocked by

None — can start immediately. (Conftest fix forcing `USE_FIXTURE_FHIR=1`
is already in.)

## User stories addressed

UC-2 per-patient brief is the headline workflow for the demo
(`ARCHITECTURE.md` §UC-2). This case is the regression-canary for
that path — without it green there is no automated coverage that the
demo's headline scenario continues to work.
