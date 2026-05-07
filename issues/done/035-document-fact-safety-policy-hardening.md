## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Harden the product behavior and tests around document-sourced facts. The agent
should treat extracted document values as source evidence requiring clinician
review, not automatic chart truth. Low-confidence clinically important values
should not become the basis for confident evidence synthesis, and final answers
should remain citation-gated.

## Acceptance criteria

- [ ] Final answer behavior distinguishes structured chart facts from extracted
      document facts.
- [ ] Extracted labs are presented as source-linked document annotations, not
      as first-class chart Observations.
- [ ] Low-confidence clinically important extracted values are surfaced as
      uncertain rather than asserted confidently.
- [ ] Mixed document/evidence flows avoid using low-confidence important values
      as the basis for guideline synthesis.
- [ ] If synthesis emits uncited clinical claims, verifier behavior regenerates
      within the existing cap and then refuses or narrows the answer.
- [ ] Tests or eval fixtures cover low-confidence extracted facts and uncited
      document-derived clinical claims.
- [ ] Existing W2 eval gate and graph integration tests continue to pass.

## Blocked by

- Blocked by `issues/031-upload-bbox-response-contract.md`

## User stories addressed

- User story 11
- User story 12
- User story 13
- User story 16
- User story 17
