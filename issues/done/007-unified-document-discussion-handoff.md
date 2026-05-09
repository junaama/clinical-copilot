## Parent PRD

`issues/prd.md`

## What to build

Make the post-upload document discussion flow work coherently for every supported document family. Successful uploads should remain discussable in chat, partial failures should remain safe and non-discussable unless meaningful extracted content exists, and cached extraction records should support follow-up questions without redoing expensive parsing or model calls.

## Acceptance criteria

- [x] The upload sentinel and classifier routing support all new document kinds and formats.
- [x] Successful uploads from HL7 ORU, HL7 ADT, XLSX, DOCX, and TIFF can be handed off to document discussion.
- [x] Follow-up document questions retrieve cached extraction content and citations across all supported formats.
- [x] Failure outcomes preserve document ids where upload succeeded and avoid chat handoff when extraction content is unavailable or unsafe to discuss.
- [x] The frontend extraction panel and source-chip behavior show coherent labels and safe empty states for all supported document families.
- [x] Tests cover post-upload handoff and cache-first follow-up behavior for at least two non-PDF formats plus one legacy PDF flow.

## Blocked by

- Blocked by `issues/002-hl7-oru-lab-upload.md`
- Blocked by `issues/003-hl7-adt-upload.md`
- Blocked by `issues/004-xlsx-workbook-upload.md`
- Blocked by `issues/005-docx-referral-upload.md`
- Blocked by `issues/006-tiff-fax-packet-upload.md`

## User stories addressed

- User story 20
- User story 26
- User story 27
- User story 28
- User story 29
- User story 33
- User story 35
- User story 36

## Worker note

Completed. Added HL7 ADT support to the cache-first `extract_document` tool path, documented the new doc_type list in the intake extractor prompt, and updated the extraction panel to render non-PDF lab payloads plus ADT details with safe empty states.
