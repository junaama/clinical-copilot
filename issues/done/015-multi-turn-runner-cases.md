## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

Extend the eval runner to handle multi-turn cases end-to-end, and author the three multi-turn golden cases that exercise the new path. Bundled because the runner without cases is empty plumbing.

Runner changes: when a case has more than one element in its `turns:` list, construct a fresh in-memory `MemorySaver` checkpointer with a unique `thread_id`, invoke the agent once per turn against that thread (each turn's prompt is sent with the conversation history accumulated by the checkpointer), and score every applicable dimension on every turn. All turns must pass for the case to pass; per-turn dimension verdicts are aggregated into a single `CaseResult` with a `multi_turn` `DimensionResult` summarizing turn-level pass rate.

Author three multi-turn golden YAML cases:
1. `golden-mt-001-eduardo-overnight-followup` — UC-2 brief, then "and the cross-cover doc's plan?", then "is the creatinine back to baseline?"
2. `golden-mt-002-triage-to-brief` — UC-1 panel triage, then "tell me about Hayes", then "their meds?"
3. `golden-mt-003-cross-patient-pivot` — Eduardo brief, then "now Park", then verifies agent re-fetches and does not leak Eduardo's data

Standardized score `multi_turn.turn_pass_rate` (fraction) attached to case trace. Runner falls back to ephemeral mode (no Langfuse) when env is unset.

See PRD "Solution" (multi-turn section), "Implementation Decisions" (multi-turn architecture), and User Stories 6, 7, 18, 28, 31.

## Acceptance criteria

- [ ] Runner detects `len(case.turns) > 1` and constructs `MemorySaver` checkpointer with a unique `thread_id` per case
- [ ] Each turn invokes the agent through the checkpointed thread so conversation history threads correctly across turns
- [ ] Every applicable dimension (substring, citation, faithfulness, trajectory) scores every turn
- [ ] All turns must pass for the case to pass overall (any-turn-fail propagates)
- [ ] `multi_turn` `DimensionResult` summarizes per-turn pass rate
- [ ] Standardized score `multi_turn.turn_pass_rate` attached to case trace
- [ ] Runner degrades gracefully to ephemeral mode when Langfuse env is unset
- [ ] Three multi-turn golden YAML cases authored: overnight follow-up, triage-to-brief, cross-patient pivot
- [ ] Cross-patient pivot case asserts agent does not leak the prior patient's data on the new patient turn
- [ ] Unit tests with stub agent cover: single-element turns runs single-turn path (backward compat), three-turn case threads `MemorySaver` state correctly, turn 2 failure causes overall case failure even if turn 3 recovers, dimension results aggregate correctly across turns
- [ ] Scoreboard shows a multi-turn column in the golden tier

## Blocked by

- Blocked by `issues/014-schema-turns-list-migration.md`

## User stories addressed

- User story 6
- User story 7
- User story 18
- User story 28
- User story 31
