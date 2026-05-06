## Parent PRD

`issues/prd-doc-recovery.md`

## What to build

Make guideline RAG answer generation fail closed when clinical claims are not
backed by ratified guideline citations. If retrieval finds citeable evidence,
the final answer must cite it visibly. If no citeable evidence is available,
the agent should give a safe evidence-gap response rather than uncited medical
advice.

The completed slice should be demoable by asking a guideline question with
evidence and seeing citations, then asking a no-evidence question and seeing a
clear refusal/evidence-gap answer.

## Acceptance criteria

- [ ] The verifier detects guideline/evidence answers that contain clinical
      recommendations but no ratified guideline citations.
- [ ] The verifier continues to reject guideline citations that were not
      fetched in the current turn.
- [ ] When citeable guideline evidence is available, the final answer includes
      at least one visible guideline citation.
- [ ] When no citeable evidence is available, the final answer explains the
      evidence gap and avoids uncited clinical recommendations.
- [ ] RAG eval or graph tests cover uncited guideline answer, unresolved
      guideline citation, successful ADA answer, and successful KDIGO answer.
- [ ] Existing chart-answer verifier behavior remains unchanged.

## Blocked by

- Blocked by `issues/027-guideline-citation-wire-contract.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 18
- User story 19
- User story 29
