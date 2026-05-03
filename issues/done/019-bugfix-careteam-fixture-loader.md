## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

Independent bug-fix slice. Investigate and fix the CareTeam fixture loader bug surfaced by the eval suite: UC-1 panel triage cases (`smoke-004-triage-panel`, `golden-w10-001-panel-scan`) return "no patients on your CareTeam panel" because the `dr_smith` user has no CareTeam membership in fixture mode. This blocks any UC-1 evaluation regardless of agent quality.

Likely location: `agent/src/copilot/fixtures.py` (the synthetic CareTeam payload) or `agent/src/copilot/care_team.py` (the lookup logic). The fix should ensure that running with `USE_FIXTURE_FHIR=1` returns a populated CareTeam roster for `dr_smith` covering the fixture patient cohort (Eduardo, Hayes, Park, Robert, etc.).

Re-run the existing 22-case suite after the fix and document the actual pass-rate lift, especially on UC-1 triage cases.

This slice runs in parallel to the eval-scaffolding stream — it touches `agent/src/copilot/fixtures.py` and/or `care_team.py` only.

See PRD "Problem Statement" (the two systemic bugs), "Solution" (parallel bug-fix stream), and User Story 22.

## Acceptance criteria

- [ ] Root cause identified and documented in commit message: which fixture / lookup path returns empty for `dr_smith`
- [ ] Fix lands in `agent/src/copilot/fixtures.py` or `agent/src/copilot/care_team.py`
- [ ] Running existing seeded `dr_smith` panel case (`golden-w1-001-dr-smith-panel`) continues to pass
- [ ] `smoke-004-triage-panel` and `golden-w10-001-panel-scan` no longer fail on "no patients on your CareTeam panel" — they pass or fail on substantive grounds
- [ ] Fixture CareTeam roster covers the patients referenced by the existing case suite (Eduardo, Hayes, Park, Robert at minimum)
- [ ] No regression on cases that were previously passing
- [ ] `agent/tests/test_seed_careteam.py` continues to pass (production seed path is not affected by fixture-mode fix)

## Blocked by

None — can start immediately. Independent of all eval-scaffolding slices.

## User stories addressed

- User story 22
