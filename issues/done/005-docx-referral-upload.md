## Parent PRD

`issues/prd.md`

## What to build

Add an end-to-end upload and extraction path for DOCX referral letters. A clinician should be able to upload a referral letter from the week-2 asset pack and receive a referral-specific extraction that captures who is referring, who is receiving, why the referral exists, pertinent history, medications, allergies, labs, and requested actions, with citations back to document sections.

## Acceptance criteria

- [x] DOCX files are accepted by the upload path and routed through text-document extraction rather than visual page rendering.
- [x] DOCX text extraction preserves enough paragraph or section structure to support meaningful source citations.
- [x] Referral extraction captures referring organization/provider, receiving provider, patient identifiers, reason for referral, pertinent history, medications, allergies, pertinent labs, and requested action where present.
- [x] The upload response represents referral content as a non-lab document without forcing it into lab or intake schemas.
- [x] Source citations reference document sections or paragraphs.
- [x] Tests cover at least one `cohort-5-week-2-assets-v2/docx/*-referral.docx` file end-to-end.

## Blocked by

- Blocked by `issues/001-multi-format-upload-contract.md`

## User stories addressed

- User story 3
- User story 4
- User story 20
- User story 22
- User story 26
- User story 27
- User story 29
- User story 31
- User story 34
- User story 36
