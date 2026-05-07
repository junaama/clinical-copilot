## Parent PRD

`issues/prd-doc-recovery.md`

## What to build

Add a live smoke path that protects the exact Week 2 reliability failures
observed in the deployed browser flow. The smoke should exercise upload
mismatch handling, correct intake upload behavior, extraction/chat consistency,
and visible guideline citations for representative ADA and KDIGO prompts.

The completed slice should be runnable as a focused live check and should fail
if the demo regresses to the observed behavior: silent wrong-type upload,
panel/chat contradiction, or uncited RAG answer.

## Acceptance criteria

- [ ] The live smoke attempts to attach an intake fixture while lab mode is
      selected and asserts the mismatch is surfaced rather than silently
      processed as a lab extraction.
- [ ] The live smoke attaches an intake fixture correctly and asserts the
      intake-oriented result is visible.
- [ ] The live smoke asserts the immediate post-upload chat outcome agrees with
      the extraction panel and cites the same document reference when
      extraction succeeds.
- [ ] The live smoke asks an ADA A1c target question and asserts visible
      guideline citation output.
- [ ] The live smoke asks a KDIGO ACE/ARB question and asserts visible
      guideline citation output.
- [ ] The smoke is documented with required credentials, fixture files, and
      expected runtime/environment assumptions.
- [ ] The smoke is focused enough to run manually or in an optional live tier
      without becoming a broad end-to-end suite.

## Blocked by

- Blocked by `issues/024-upload-type-mismatch-guard.md`
- Blocked by `issues/025-canonical-upload-outcome.md`
- Blocked by `issues/026-post-upload-chat-consistency.md`
- Blocked by `issues/027-guideline-citation-wire-contract.md`
- Blocked by `issues/028-rag-citation-fail-closed.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 30
