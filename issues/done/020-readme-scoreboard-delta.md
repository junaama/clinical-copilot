## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

The narrative deliverable that ties every prior slice together. Update `agentforge-docs/README-DRAFT.md` to replace the v0 scoreboard (5/22 passed, 23%) with the v2 multi-dimensional scoreboard, and add a brief "what the evals caught" sidebar that documents the diagnostic-tool story.

The v2 scoreboard table presents per-dimension pass rates per tier (Faithfulness, Trajectory, Multi-turn, Overall) plus the gate verdict per tier. The sidebar narrates: "v0 of this suite revealed two systemic bugs (clarify-routing and CareTeam fixture loading); pass rate lifted from 23% to X% after the fixes; v2 then expanded to 32 cases across three new dimensions, landing at the per-dimension pass rates above." If the post-fix lift is smaller than the hypothesised 23% → 87%, the sidebar reports the actual number and the additional issues that surfaced.

Run the full eval suite end-to-end against the final state of all prior slices, capture the per-tier per-dimension pass rates, and write them into the scoreboard. Capture the cost (~$0.15 expected) and runtime (~5 min expected) and include them in the section.

See PRD "Solution" (final README scoreboard), "Implementation Decisions" (phasing — README delta narrates eval-driven discovery), and User Story 24.

## Acceptance criteria

- [ ] Full eval suite runs end-to-end with all 32 cases and all three new dimensions
- [ ] `agentforge-docs/README-DRAFT.md` "Eval results" section replaced with v2 multi-dimensional scoreboard
- [ ] Scoreboard table includes columns for Faithfulness, Trajectory, Multi-turn (where applicable), Overall, and Gate verdict per tier
- [ ] "What the evals caught" sidebar paragraph narrates the v0 → bug-fix → v2 journey with actual numbers (no fabricated lifts)
- [ ] Section reports the actual judge cost and runtime of the run, not estimates
- [ ] Section links to `agent/evals/{smoke,golden,adversarial}/` and to the parent PRD
- [ ] Section contains the reproduce command (`USE_FIXTURE_FHIR=1 uv run pytest evals/ -v`)
- [ ] If golden tier overall pass rate is below the 80% gate, that gap is honestly stated in the README — no green-via-low-bars

## Blocked by

- Blocked by `issues/011-faithfulness-citation-anchored.md`
- Blocked by `issues/012-faithfulness-uncited-sweep.md`
- Blocked by `issues/013-trajectory-dimension-backfill.md`
- Blocked by `issues/015-multi-turn-runner-cases.md`
- Blocked by `issues/016-new-smoke-adversarial-cases.md`
- Blocked by `issues/017-ci-gates-release-blocker.md`
- Blocked by `issues/018-bugfix-clarify-routing.md`
- Blocked by `issues/019-bugfix-careteam-fixture-loader.md`

## User stories addressed

- User story 24
