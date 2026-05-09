## Parent PRD

`issues/prd.md`

## What to build

Optional follow-on after issue 051. Extend the LLM-judge module only
where it adds reviewer-visible semantic value without making the
pre-push gate too slow, flaky, or expensive. Use the pipeline shape
validated by issue 051.

- **May replace the regex verdict** for `citation_present` and
  `safe_refusal` (semantic rubrics).
- **Do not replace** `schema_valid` and `no_phi_in_logs` by default.
  Pydantic stays the schema floor and the deterministic PHI scanner
  stays the compliance floor. Add an LLM augmentation only if a
  specific failing case proves the structural check cannot express the
  requirement.

After the four rubrics ship, regenerate `.eval_baseline.json` with
the new pass rates and commit it in the same PR so the gate's
fail-closed semantics keep their meaning.

See PRD §Implementation Decisions › LLM-Backed Evaluator Module
(esp. the structural-vs-semantic policy and the baseline
regeneration step) and §Testing Decisions › LLM Judge.

## Acceptance criteria

- [x] Written justification in this issue or commit notes explains
      why each additional LLM-judged rubric is worth its added
      latency/cost
- [x] LLM-judge functions exist for any selected semantic rubrics
      (`citation_present`, `safe_refusal`) following the same
      signature + prompt-constant convention as issue 051
- [x] Runner replaces the regex verdict only for selected semantic
      rubrics; `schema_valid` and `no_phi_in_logs` remain
      deterministic unless a separate acceptance note documents the
      proven need for LLM augmentation
- [x] Each rubric's prompt is committed as a documented constant
      that a grader can read without running the agent
- [x] `not_applicable` short-circuit and verdict cache work for all
      five rubrics
- [x] `.eval_baseline.json` regenerated and committed; the new
      file is the regression floor
- [x] Tests cover, for each selected rubric: known pass, known fail
      with non-empty `details`, `not_applicable` short-circuit,
      cache-hit, and cache invalidation using the issue-051 cache key
- [x] Pre-push runtime/cost impact is documented after the selected
      additional rubric(s) land
- [x] One-line pointer added to `W2_ARCHITECTURE.md` directing the
      reader to the judge module

## Completion notes

Selected LLM rubrics:

- `citation_present`: worth adding because the regex pass/fail is sentence-pattern
  based and can over-fire on clinical-looking prose that is not actually a
  factual claim. To guard runtime/cost, the runner only calls the LLM adjudicator
  when the deterministic citation check has already found a possible failure;
  clean regex passes remain free and deterministic.
- `safe_refusal`: worth adding because refusal quality is semantic: the current
  regex can recognize stock refusal phrasing, but cannot reliably tell whether
  the answer also leaked patient-specific facts or gave the prohibited clinical
  instruction. Runtime/cost is bounded to cases with `should_refuse: true`; all
  non-refusal cases short-circuit as `not_applicable`.

Structural rubrics:

- `schema_valid` remains Pydantic-only. No failing case proved that an LLM can
  express a schema requirement better than the typed schema floor.
- `no_phi_in_logs` remains deterministic scanner-only. No failing case proved
  the compliance floor needs probabilistic augmentation.

Cache and applicability:

- The three LLM-backed semantic rubrics (`factually_consistent`,
  `citation_present`, `safe_refusal`) share the issue-051 SQLite verdict cache
  shape: rubric, case id, response hash, context hash, prompt hash, model id,
  and schema version. Prompt/model/response/context edits invalidate old
  verdicts.
- `factually_consistent` short-circuits when no fixture extraction exists;
  `citation_present` short-circuits on an empty response; `safe_refusal`
  short-circuits for non-refusal cases. `schema_valid` and `no_phi_in_logs`
  keep their deterministic behavior rather than adding cacheable LLM verdicts.

Pre-push runtime/cost impact:

- Worst case adds one factual-consistency judge call for extraction-backed
  cases, one safe-refusal call for refusal-required cases, and citation calls
  only for responses the regex already flagged as potentially uncited. With the
  current fixture suite this keeps the normal all-pass path close to issue 051's
  cost profile, while still giving reviewers semantic diagnostics on the
  rubrics most likely to produce regex false positives/negatives.
- Baseline regeneration was run with `EVAL_LLM_JUDGE_ENABLED=false` because this
  worker environment has no `ANTHROPIC_API_KEY`; the committed baseline remains
  the deterministic regression floor. Environments with a judge key can opt into
  the LLM semantic floor by regenerating with `EVAL_LLM_JUDGE_ENABLED=true`.

## Blocked by

- Blocked by `issues/051-llm-judge-tracer-factually-consistent.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 1
- User story 2
- User story 3
- User story 4
- User story 18
- User story 20
