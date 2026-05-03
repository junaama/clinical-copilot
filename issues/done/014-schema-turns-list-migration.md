## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

Unify the case YAML schema around a single `turns: [...]` list shape. Single-turn cases become a one-element list; multi-turn cases (added in `issues/015-multi-turn-runner-cases.md`) use multiple elements. Each turn carries its own `prompt`, optional `must_contain`, optional `must_cite`, and optional `trajectory.required_tools`.

Update the `Case` dataclass and YAML loader to parse the new shape. Migrate all 22 existing case files in `agent/evals/{smoke,golden,adversarial}/*.yaml` to the unified schema — single-turn cases get rewritten as one-element `turns:` lists with all per-turn fields nested correctly.

Runner changes: detect single-turn (one element) and run as today. Multi-turn handling lands in slice 015.

Backward compatibility is verified by tests, not by carrying two schemas — the migration is one-shot and complete in this slice.

See PRD "Implementation Decisions" (schema decisions) and User Stories 8, 9, 33.

## Acceptance criteria

- [ ] `Case` dataclass carries a `turns: list[Turn]` field
- [ ] `Turn` dataclass carries fields for `prompt`, optional `must_contain`, optional `must_cite`, optional `trajectory.required_tools`
- [ ] YAML loader rejects legacy single-prompt shape with a clear error message pointing to the migration
- [ ] All 22 existing case YAML files rewritten to the unified `turns: [{...}]` shape with all per-turn fields nested correctly
- [ ] Runner detects `len(case.turns) == 1` and runs single-turn semantics (multi-turn handling deferred to slice 015)
- [ ] All 22 cases produce the same overall pass/fail verdict and per-dimension verdicts they did before the migration (no regressions in pass rate from the schema change alone)
- [ ] Unit tests cover: YAML loader produces correct `Case` from new shape, single-element turns list runs single-turn path, dimension scoring still applies per turn

## Blocked by

- Blocked by `issues/010-per-dimension-result-schema.md`

## User stories addressed

- User story 8
- User story 9
- User story 33
