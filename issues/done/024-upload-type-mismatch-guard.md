## Parent PRD

`issues/prd-doc-recovery.md`

## What to build

Prevent clinicians from accidentally running the wrong extraction path when the
selected upload type does not match the attached document. This slice should
make the selected document type obvious, detect likely lab-vs-intake mismatches
without a live VLM call, warn or require confirmation before upload continues,
and return structured mismatch information from the upload API when the backend
can confidently identify the mismatch.

The completed slice should be demoable by attempting to attach an intake form
while "Lab PDF" is selected: the user sees a clear mismatch affordance instead
of silently receiving a lab extraction.

## Acceptance criteria

- [ ] The upload widget makes the active document type visible near the drop
      zone before a file is picked or dropped.
- [ ] File picker and drag-and-drop uploads run the same deterministic
      document-type guard.
- [ ] The guard can identify obvious fixture mismatches, including an intake
      form selected as `lab_pdf` and a lab PDF selected as `intake_form`,
      without calling the live VLM.
- [ ] When the selected and detected document types disagree, the UI clearly
      offers correction or explicit confirmation before upload proceeds.
- [ ] Keyboard users can review and resolve the mismatch flow.
- [ ] The backend upload path returns structured mismatch details when it can
      confidently detect an inconsistency.
- [ ] The unchanged happy path remains direct: a lab-looking file selected as
      `lab_pdf` and an intake-looking file selected as `intake_form` upload
      normally.
- [ ] Tests cover file picker, drag-and-drop, keyboard handling, structured API
      mismatch responses, and unchanged happy paths.

## Blocked by

None - can start immediately.

## User stories addressed

Reference by number from the parent PRD:

- User story 1
- User story 2
- User story 3
- User story 4
- User story 5
- User story 6
- User story 9
- User story 24
