## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

The headline tracer bullet for the new eval methodology. Add a `FaithfulnessJudge` module that runs after the agent produces a response: for every `<cite ref="..."/>` citation the agent emitted, the judge fetches the cited resource from what the agent retrieved, and asks a Haiku 4.5 LLM whether that resource actually supports the specific claim attached to the citation. Per-citation verdicts plus reasoning are returned as a structured result.

Wire the judge into the runner so every case automatically gets a `faithfulness` `DimensionResult`. Emit a Langfuse child span per citation (named `judge:faithfulness:citation:{ref}`) carrying the claim, cited resource, verdict, and reasoning. On pytest failure, surface the first few unsupported-citation reasonings inline along with a Langfuse trace URL.

This slice lands faithfulness using the *citation-anchored* pass only — uncited-claim sweep ships in `issues/012-faithfulness-uncited-sweep.md`. A case passes faithfulness in this slice when 100% of its citations are judged supported.

See PRD "Solution" (faithfulness section) and "Implementation Decisions" (architectural decisions, judge model).

## Acceptance criteria

- [ ] `FaithfulnessJudge` module exists with one async public method that accepts response text and the dictionary of fetched FHIR resources, and returns a structured result
- [ ] Module accepts an injected LLM client so tests can use a stub
- [ ] Citation parsing extracts `<cite ref="..."/>` references from response text and ignores malformed citations cleanly (no crash)
- [ ] Judge runs Haiku 4.5 by default, configurable via settings
- [ ] Runner invokes the judge after agent inference and attaches the result as a `DimensionResult` named `faithfulness` to the `CaseResult`
- [ ] Each judge call emits a Langfuse child span on the case trace named `judge:faithfulness:citation:{ref}`
- [ ] Standardized score names `faithfulness.citations_supported` (fraction) attached to the case trace
- [ ] Pytest failure output for a faithfulness failure includes up to 3 judge reasonings inline plus a Langfuse trace URL
- [ ] Unit tests use a stub LLM and cover: all citations supported → pass, one citation unsupported → fail with reasoning surfaced, malformed `<cite>` syntax → clean error not crash, response with zero citations → pass
- [ ] Existing 22 cases run end-to-end with the new dimension; scoreboard shows a faithfulness column with realistic per-tier pass rates

## Blocked by

- Blocked by `issues/010-per-dimension-result-schema.md`

## User stories addressed

- User story 2
- User story 4
- User story 19
- User story 20
- User story 21
- User story 25
- User story 27
- User story 29
