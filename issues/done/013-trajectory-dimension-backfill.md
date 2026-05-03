## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

Add the trajectory dimension end-to-end as a single tracer bullet — module, runner integration, and YAML backfill all in one slice (because the dimension at empty-required is plumbing without signal).

The `TrajectoryEvaluator` is a pure function: given the list of tool-call records the LangGraph agent produced and the list of `required_tools` declared in the case YAML, return a structured result containing missing required tools, present-but-not-required tools, and a binary pass field. Set-membership only — no ordering, no argument matching, no forbidden-tools list.

Wire into the runner so every case automatically gets a `trajectory` `DimensionResult` (cases with empty `required_tools` always pass that dimension). Attach the standardized score `trajectory.required_present` (binary) to the case trace.

Backfill `required_tools` on the high-signal subset of existing cases (~12–15 of 22) — UC-2 brief cases, UC-1 triage cases, imaging lookup cases. The remainder keep `required_tools: []`.

See PRD "Solution" (trajectory section) and "Implementation Decisions" (set-membership semantics).

## Acceptance criteria

- [ ] `TrajectoryEvaluator` module exists with one pure function that takes tool calls + required tool names and returns a structured result
- [ ] Result fields: missing required tools, present-but-not-required tools, binary pass
- [ ] Runner extracts `tool_calls` from LangGraph state and invokes the evaluator
- [ ] `DimensionResult` named `trajectory` attached to every `CaseResult`
- [ ] Standardized score `trajectory.required_present` attached to case trace
- [ ] Empty `required_tools` always passes (no false negatives on cases that don't care about trajectory)
- [ ] At least 12 existing case YAMLs backfilled with realistic `required_tools` lists (UC-2 brief, UC-1 triage, imaging)
- [ ] Unit tests cover: required all present → pass, one required missing → fail with that tool named, extra tools allowed → pass, empty required → pass regardless of trajectory
- [ ] Scoreboard shows a trajectory column with non-trivial pass rate after backfill

## Blocked by

- Blocked by `issues/010-per-dimension-result-schema.md`

## User stories addressed

- User story 5
- User story 11
- User story 30
