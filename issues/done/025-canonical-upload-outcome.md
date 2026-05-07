## Parent PRD

`issues/prd-doc-recovery.md`

## What to build

Make the upload response the single authoritative source of truth for the
document's status. The extraction panel, upload error state, and synthetic
post-upload chat handoff should all derive from the same canonical outcome:
successful extraction, partial failure, upload/document-reference failure,
authorization failure, or extraction failure.

The completed slice should be demoable by forcing each major failure mode and
seeing the panel and chat agree. A successful panel result must not coexist
with a chat turn that says the same document cannot be read.

## Acceptance criteria

- [ ] The upload response carries enough status metadata for the UI to
      distinguish requested type, effective type, document reference,
      extraction status, persistence/document-reference status, and user-safe
      failure reason.
- [ ] The extraction panel renders from the canonical effective type and
      canonical status, not from partial payload inference alone.
- [ ] A failed or partially failed upload does not render as an empty
      successful extraction.
- [ ] The app injects a synthetic post-upload chat turn only when the canonical
      outcome says the document is available for agent discussion.
- [ ] Upload, extraction, document-reference, and authorization failures produce
      clear user-safe UI errors without raw exception details.
- [ ] Panel and chat behavior agree for successful lab uploads, successful
      intake uploads, extraction failures, document-reference failures, and
      authorization failures.
- [ ] Tests cover the application shell handoff and extraction panel behavior
      for success and failure outcomes.

## Blocked by

- Blocked by `issues/024-upload-type-mismatch-guard.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 7
- User story 8
- User story 10
- User story 11
- User story 14
- User story 15
- User story 16
- User story 23
