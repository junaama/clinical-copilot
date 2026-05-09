## Parent PRD

`issues/prd.md`

## What to build

Add an end-to-end upload and extraction path for multipage TIFF fax packets. A clinician should be able to upload a TIFF packet from the week-2 asset pack, have its pages normalized in order for visual extraction, and receive a safe structured result with page-aware citations and low-confidence handling for ambiguous scan content.

## Acceptance criteria

- [ ] TIFF files are accepted by the upload path and routed to the visual document path.
- [ ] Multipage TIFF packets are converted into ordered page images suitable for VLM extraction.
- [ ] Page order is preserved in extraction prompts, source citations, and any returned page-level references.
- [ ] TIFF extraction produces safe low-confidence flags for ambiguous visual values rather than asserting guesses as facts.
- [ ] Existing PDF, PNG, and JPEG visual extraction behavior remains unchanged.
- [ ] Tests cover at least one `cohort-5-week-2-assets-v2/tiff/*-fax-packet.tiff` file end-to-end.

## Blocked by

- Blocked by `issues/001-multi-format-upload-contract.md`

## User stories addressed

- User story 1
- User story 2
- User story 20
- User story 21
- User story 27
- User story 29
- User story 30
- User story 32
- User story 34
- User story 36
