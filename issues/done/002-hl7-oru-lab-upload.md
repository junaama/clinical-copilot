## Parent PRD

`issues/prd.md`

## What to build

Add an end-to-end upload and extraction path for HL7 v2 ORU lab messages. A clinician should be able to upload an ORU message from the week-2 asset pack and receive structured lab observations with order context, notes, source citations, cache persistence, and the same lab-oriented downstream behavior used by existing lab extractions where fields are compatible.

## Acceptance criteria

- [ ] HL7 ORU files are accepted by the upload path and classified as deterministic text messages rather than visual documents.
- [ ] ORU parsing extracts patient identifiers, order context, OBR details, OBX observations, LOINC codes, values, units, reference ranges, abnormal flags, status, timestamps, and NTE notes where present.
- [ ] ORU observations map into the lab extraction shape or a lab-compatible output used by the upload response and cache.
- [ ] Source citations reference HL7 segment and field locations rather than visual bounding boxes.
- [ ] The deterministic ORU path does not call the VLM.
- [ ] Tests cover at least one `cohort-5-week-2-assets-v2/hl7v2/*-oru-r01.hl7` file end-to-end.

## Blocked by

- Blocked by `issues/001-multi-format-upload-contract.md`

## User stories addressed

- User story 10
- User story 11
- User story 12
- User story 20
- User story 24
- User story 25
- User story 27
- User story 29
- User story 31
- User story 34
- User story 36
