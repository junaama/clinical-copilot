## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Create the 50-case eval suite with 5 boolean rubric categories, a pre-push git hook that blocks regressions, and a separate full-eval target for live VLM testing.

**Components:**

**1. Eval cases (50 total):**
- 10 lab PDF extraction cases
- 8 intake form extraction cases
- 8 evidence retrieval cases
- 6 supervisor routing cases
- 6 citation contract cases (mixed source types)
- 6 safe refusal cases
- 3 no-PHI-in-logs cases
- 3 Week 1 regression guards

Cases use pre-computed fixture extractions (no live VLM). Fixture documents from `example-documents/`.

**2. Boolean rubric evaluators:**
- `schema_valid` — extraction conforms to Pydantic schema
- `citation_present` — every clinical claim has a citation
- `factually_consistent` — cited values match source data
- `safe_refusal` — agent refuses correctly for out-of-scope / unsafe cases
- `no_phi_in_logs` — traces contain no raw patient identifiers

**3. Pre-push git hook (`.git/hooks/pre-push`):**
- Check changed files via `git diff --name-only`
- Skip if only docs/UI/config changed
- Run `pytest agent/evals/` if agent/src/, agent/evals/, or data/guidelines/ changed
- Compare results against `.eval_baseline.json`
- FAIL if any category drops >5% from baseline or below absolute threshold
- Thresholds: schema_valid >=95%, citation_present >=90%, factually_consistent >=90%, safe_refusal >=95%, no_phi_in_logs 100%

**4. Full eval target:**
- `make eval-full` or `pytest agent/evals/ --live-vlm`
- Runs live VLM extraction on fixture documents
- Compares against expected extractions to detect model drift
- Not in the push gate

**5. Baseline file:**
- `.eval_baseline.json` committed to repo with per-category pass rates
- Updated when baseline intentionally changes (e.g., after adding cases)

## Acceptance criteria

- [ ] 50 eval cases written as YAML (following existing eval case format)
- [ ] 5 boolean rubric evaluator functions implemented
- [ ] Pre-push hook script installs and runs correctly
- [ ] Hook skips on irrelevant file changes (docs, UI, scripts)
- [ ] Hook fails on >5% regression in any boolean category
- [ ] Hook fails if absolute threshold breached
- [ ] `.eval_baseline.json` committed with initial pass rates
- [ ] `make eval-full` runs live VLM (separate from gate)
- [ ] Gate runs in <30 seconds (fixture-based, no API calls)
- [ ] Introducing a deliberate regression (e.g., removing a citation) causes the hook to fail
- [ ] Unit tests for gate logic: threshold checking, regression detection, baseline comparison

## Blocked by

- `issues/009-supervisor-workers-classifier.md` (full pipeline must work to generate realistic eval cases)

## User stories addressed

- User story 13 (pre-push hook blocks regressions)
- User story 14 (runs only on relevant changes)
- User story 15 (fixture-based, no live VLM)
- User story 16 (separate eval-full target)
- User story 19 (grader introduces regression, gate catches it)
