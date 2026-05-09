## Parent PRD

`issues/prd.md`

## What to build

Tracer-bullet slice through the entire LLM-judge pipeline using
`factually_consistent` as the single rubric (the grader's stated
minimum bar). The point of doing one rubric end-to-end before fanning
out is to validate the module shape, the prompt-as-code convention,
the LLM-client integration, the verdict cache, and the runner wiring
on a single example. If any of those are wrong, only one rubric
needs rework.

Build `agent/src/copilot/eval/llm_judge.py` with `claude-sonnet-4-6`
pinned as a module-level constant, the rubric prompt as a documented
constant in the same file, an LLM call routed through
`agent/src/copilot/llm.py` (so cost tracking + Langfuse tracing are
inherited), and a SQLite verdict cache keyed by
`(rubric, case_id, response_hash, fixture_extraction_hash,
prompt_hash, model_id, judge_schema_version)`. Wire the runner to call the LLM
judge for `factually_consistent` (replacing the regex verdict for
that rubric only) behind feature flag
`EVAL_LLM_JUDGE_ENABLED` (default `True`). Fail-closed on missing
`ANTHROPIC_API_KEY`.

See PRD §Implementation Decisions › LLM-Backed Evaluator Module and
§Testing Decisions › LLM Judge for the full spec.

## Acceptance criteria

- [ ] `agent/src/copilot/eval/llm_judge.py` exists with the model
      pin as a top-level constant (greppable string
      `claude-sonnet-4-6`)
- [ ] `factually_consistent` LLM judge exposes a function with the
      same signature shape as the regex evaluator and returns a
      `RubricResult`
- [ ] Prompt for `factually_consistent` is a documented multi-line
      constant in the same module with a top-of-file comment
      explaining the prompting approach
- [ ] LLM call uses `temperature=0` and goes through the existing
      `agent/src/copilot/llm.py` factory
- [ ] Verdict cache key includes `rubric`, `case_id`,
      `response_hash`, `fixture_extraction_hash`, `prompt_hash`,
      `model_id`, and `judge_schema_version`; re-runs against
      unchanged inputs skip the LLM call
- [ ] Changing the prompt text, model pin, fixture extraction, or
      judge schema version invalidates the cached verdict
- [ ] `not_applicable` cases short-circuit without invoking the LLM
- [ ] `EVAL_LLM_JUDGE_ENABLED=false` falls back to the existing
      regex layer; missing API key triggers a fail-closed gate exit
      with a clear message
- [ ] Runner aggregation contract preserved (no changes to
      `aggregate_pass_rates` consumers)
- [ ] Tests in `agent/tests/eval/test_llm_judge.py` cover: known
      pass, known fail with non-empty `details`, `not_applicable`
      short-circuit, cache-hit (no LLM call observed), and cache
      invalidation when prompt/model/input hash changes
- [ ] W2 CLI gate (`cd agent && uv run python -m
      copilot.eval.w2_baseline_cli check`) exits non-zero with a
      clear message when LLM judging is enabled but the required API
      key is missing

## Blocked by

None - can start immediately.

## User stories addressed

Reference by number from the parent PRD:

- User story 1
- User story 2
- User story 3
- User story 4
- User story 5
- User story 19
