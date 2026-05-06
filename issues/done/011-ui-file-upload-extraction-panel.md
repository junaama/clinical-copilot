## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Add a file upload widget and an extraction results panel to the existing copilot-ui React app.

**File upload widget:**
- Drag-and-drop zone or file picker button
- Scoped to the active patient (patient must be resolved before upload is enabled)
- Accepts: PDF, PNG, JPEG only (client-side validation before upload)
- Size limit: 20 MB (client-side check)
- Calls agent's `POST /upload` endpoint with file + patient_id + doc_type
- Shows upload progress and success/error state

**Agent upload endpoint:**
- `POST /upload` on the agent server (FastAPI)
- Accepts multipart form data: file, patient_id, doc_type
- Stores in OpenEMR via DocumentClient
- Triggers extraction (calls `extract_document` tool)
- Returns extraction result to the UI
- Injects system message into conversation state for the classifier

**Extraction results panel:**
- Renders structured extraction data (lab values or intake fields)
- Table format: field name, value, unit, reference range, abnormal flag
- Confidence badges: green (high), yellow (medium), red (low)
- Collapsible sections for intake form (demographics, medications, allergies, etc.)
- Appears alongside the chat when an extraction is available

## Acceptance criteria

- [ ] File upload widget visible when a patient is active
- [ ] Client-side validation: only PDF/PNG/JPEG accepted, ≤20MB
- [ ] Upload calls `POST /upload` and shows progress
- [ ] Agent `POST /upload` endpoint stores document and triggers extraction
- [ ] System message injected into chat state after upload
- [ ] Extraction results panel displays structured lab values with confidence badges
- [ ] Extraction results panel displays intake form fields in collapsible sections
- [ ] Error states handled: upload failure, extraction failure, invalid file type

## Blocked by

- `issues/006-extraction-tool-end-to-end.md` (extraction pipeline must work)

## User stories addressed

- User story 1 (upload lab PDF through Co-Pilot)
- User story 3 (structured panel with confidence indicators)
- User story 5 (upload intake form)
