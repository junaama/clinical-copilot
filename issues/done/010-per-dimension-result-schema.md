## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

Foundational refactor of the eval result shape so every downstream dimension can attach to the same scaffolding. Introduce a `DimensionResult` dataclass and extend `CaseResult` with a `dimensions: dict[str, DimensionResult]` field. Refactor the existing substring (required-facts) and citation-completeness checks to emit `DimensionResult`s rather than ad-hoc dict failures. The existing per-case pass/fail becomes the AND of all dimension verdicts.

Add a per-tier scoreboard renderer that aggregates dimension results across cases and prints a tier × dimension × overall table. Existing 22 cases must still run and produce identical pass/fail verdicts after the refactor — only the *shape* of the result changes, not the semantics.

See PRD "Implementation Decisions" for module surfaces and "Solution" for the scoreboard target.

## Acceptance criteria

- [ ] `DimensionResult` dataclass exists with fields for name, binary pass, optional continuous score, and a free-form details dict
- [ ] `CaseResult` carries a `dimensions: dict[str, DimensionResult]` field
- [ ] Existing substring (required-facts) check emits a `DimensionResult` with name `substring`
- [ ] Existing citation-completeness check emits a `DimensionResult` with name `citation`
- [ ] Per-case overall pass = AND of all `DimensionResult.passed` values
- [ ] A scoreboard renderer prints a per-tier per-dimension pass-rate table after the run
- [ ] All 22 existing cases produce the same overall pass/fail verdict as before the refactor
- [ ] Unit tests cover `DimensionResult` aggregation, AND-gating across dimensions, and per-tier scoreboard math

## Blocked by

None — can start immediately.

## User stories addressed

- User story 1
- User story 32
- User story 34
