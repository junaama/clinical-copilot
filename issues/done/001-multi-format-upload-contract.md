## Parent PRD

`issues/prd.md`

## What to build

Establish the end-to-end upload contract for multi-format document ingestion while preserving the existing lab PDF and intake form behavior. This slice should separate clinical document kind from physical source format, update upload validation and response typing, and make unsupported formats fail safely before extraction. It is the foundation that later format-specific slices plug into.

## Acceptance criteria

- [ ] Upload validation accepts the existing PDF, PNG, and JPEG flows plus the new required extensions and MIME hints for TIFF, DOCX, XLSX, and HL7.
- [ ] Backend format detection returns a normalized source format and document kind without requiring every caller to inspect raw magic bytes.
- [ ] Existing `lab_pdf` and `intake_form` upload callers remain backward-compatible.
- [ ] Unsupported, empty, and oversized files return safe user-facing errors without raw parser exception text.
- [ ] The frontend file picker, validation copy, upload API types, and upload tests reflect the expanded supported-format list.
- [ ] Existing PDF lab and intake image upload tests still pass.

## Blocked by

None - can start immediately

## User stories addressed

- User story 15
- User story 16
- User story 17
- User story 18
- User story 19
- User story 31
- User story 32
- User story 33
- User story 35
- User story 37
