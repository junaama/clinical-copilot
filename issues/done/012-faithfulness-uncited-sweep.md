## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

Extend the `FaithfulnessJudge` from `issues/011-faithfulness-citation-anchored.md` with an uncited-claim sweep pass. After the per-citation verdicts run, make one additional Haiku 4.5 call asking the judge to enumerate any factual *clinical* claims in the response that lack a `<cite>` tag. The sweep prompt must enumerate what counts as a clinical claim (vitals values, dose/frequency, lab results, medication status, encounter facts) so hedging language and clarification questions are not falsely flagged.

The combined faithfulness verdict becomes: pass = 100% of citations supported AND zero uncited clinical claims flagged. The judge result gains a new field listing flagged uncited claims with their text, and the standardized score `faithfulness.uncited_claims` (count, lower is better) is attached to the case trace. Pytest failure output for an uncited-claim failure surfaces the flagged claim text inline.

See PRD "Solution" (faithfulness hybrid approach) and User Story 26.

## Acceptance criteria

- [ ] `FaithfulnessJudge.judge` returns a result that includes a list of uncited clinical claims (claim text only)
- [ ] Sweep prompt enumerates clinical-claim categories (vitals, dose/frequency, lab results, medication status, encounter facts) so hedging and clarification text is not flagged
- [ ] Combined faithfulness pass = 100% citations supported AND zero uncited clinical claims
- [ ] One Langfuse child span per case named `judge:faithfulness:uncited_sweep` carrying the sweep prompt, response, and flagged claims
- [ ] Standardized score `faithfulness.uncited_claims` attached to the case trace
- [ ] Pytest failure output for an uncited-claim failure surfaces flagged claim text
- [ ] Unit tests with stub LLM cover: zero uncited claims → pass, one uncited clinical claim flagged → fail, hedging language not flagged as clinical claim → pass
- [ ] All 22 existing cases run end-to-end; faithfulness pass rates may shift downward as the stricter criterion is applied — that shift is documented in the run output

## Blocked by

- Blocked by `issues/011-faithfulness-citation-anchored.md`

## User stories addressed

- User story 4
- User story 26
