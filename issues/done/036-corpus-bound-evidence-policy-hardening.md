## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Harden the evidence-retrieval behavior so guideline answers are explicitly
bound to the indexed corpus and framed as clinician decision support. If the
retriever does not find relevant evidence, the agent should say the current
corpus does not support the answer rather than filling the gap from pretrained
knowledge.

## Acceptance criteria

- [ ] Evidence answers cite retrieved guideline chunks for every clinical
      evidence claim.
- [ ] No-evidence or weak-evidence retrieval outcomes produce an explicit
      corpus-bound limitation instead of uncited medical guidance.
- [ ] Recommendation-like language is framed as evidence-grounded clinician
      decision support, not autonomous treatment decision or order entry.
- [ ] Unsafe or unsupported action requests produce safe refusal or narrowing.
- [ ] Tests or eval fixtures cover no-relevant-guideline behavior and unsafe
      recommendation pressure.
- [ ] Existing W2 evidence/citation eval categories continue to pass.

## Blocked by

None - can start immediately.

## User stories addressed

- User story 14
- User story 15
- User story 16
- User story 17
- User story 19
