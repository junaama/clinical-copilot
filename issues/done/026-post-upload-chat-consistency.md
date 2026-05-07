## Parent PRD

`issues/prd-doc-recovery.md`

## What to build

Ensure post-upload chat answers use the same uploaded document evidence that
the extraction panel just displayed. The agent should prefer upload-time
extraction/cache data for the current document, cite the canonical
`DocumentReference`, and avoid falling back to non-citeable synthetic ids or
fresh document reads that can contradict a successful upload.

The completed slice should be demoable by uploading a fixture document and
seeing the immediate chat turn summarize the same extraction the panel shows,
with matching document citations.

## Acceptance criteria

- [ ] Post-upload chat uses upload-time extraction/cache data before attempting
      a document download or re-extraction.
- [ ] The document reference in upload response, extraction cache, upload
      sentinel, tool output, and final chat citation is the same canonical
      reference.
- [ ] If any fallback or synthetic document identifier appears in legacy state,
      it is treated as non-citeable and does not pass verifier checks as a real
      EHR document.
- [ ] Successful post-upload answers cite the uploaded document using
      `DocumentReference/<id>` plus available page/field/value metadata.
- [ ] Failed or unavailable extraction outcomes do not produce a synthetic
      "walk me through what's notable" chat turn.
- [ ] Tests prove post-upload chat does not contradict the extraction panel
      and does not re-read/re-extract when upload-time extraction is available.
- [ ] Graph-level coverage asserts a terminal assistant answer cites the real
      uploaded document reference.

## Blocked by

- Blocked by `issues/025-canonical-upload-outcome.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 10
- User story 12
- User story 13
- User story 25
- User story 26
- User story 27
