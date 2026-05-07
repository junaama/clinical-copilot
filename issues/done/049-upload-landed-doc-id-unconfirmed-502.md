## Parent PRD

`issues/prd-doc-recovery.md`

## What to build

Fix the live upload journey where the browser shows
`Upload failed (HTTP 502): upload landed but the document id couldn't be confirmed; please re-attach`.
The upload flow should either recover the real OpenEMR document reference after
the upload lands or fail before any downstream extraction/chat state is
created. A completed slice should be verifiable from the standalone UI: select a
patient, upload a supported PDF or image, and see a single canonical outcome
that the upload widget, extraction panel, and post-upload chat agree on.

## Acceptance criteria

- [ ] Reproduce the deployed failure path with a synthetic patient and uploaded
      document, capturing the status returned by the upload endpoint.
- [ ] When OpenEMR stores the uploaded bytes but the initial response cannot
      provide a document id, the backend attempts recovery by listing the
      patient's recent documents and matching by filename/category/recency.
- [ ] On recovery success, the upload response includes the real canonical
      document reference and does not expose a synthetic `openemr-upload-*`
      identifier.
- [ ] On recovery failure, the upload response is a stable user-safe failure
      that does not create an extraction panel, does not inject a post-upload
      chat turn, and leaves the upload widget ready for retry.
- [ ] The frontend renders the same canonical upload outcome across the upload
      widget, extraction panel, and chat handoff.
- [ ] Tests cover normal upload success, landed-but-id-unconfirmed recovery
      success, recovery failure, and the frontend no-handoff failure behavior.
- [ ] A live or deployed smoke check verifies that the browser upload journey no
      longer gets stuck on the observed HTTP 502 document-id confirmation error.

## Blocked by

None - can start immediately.

## User stories addressed

- User story 10
- User story 13
- User story 14
- User story 23
- User story 25
- User story 26
- User story 27
- User story 30
