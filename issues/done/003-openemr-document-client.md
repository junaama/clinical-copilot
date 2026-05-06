## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Create a document client that wraps OpenEMR's Standard REST API for document operations. This client handles upload, listing, and download of patient documents.

**Module:** `agent/src/copilot/extraction/document_client.py`

**Methods:**
- `upload(patient_id, file_data, filename, category) → (ok, document_id, error, latency_ms)` — POST /api/patient/{pid}/document
- `list(patient_id, category?) → (ok, documents_list, error, latency_ms)` — GET /api/patient/{pid}/document
- `download(patient_id, document_id) → (ok, file_bytes, mimetype, error, latency_ms)` — GET /api/patient/{pid}/document/{did}

**Requirements:**
- Uses the same bearer token as the existing FhirClient (passed at construction or resolved from the same token source)
- File type validation before upload: magic-byte check for PDF, PNG, JPEG. Reject everything else.
- Size limit: 20 MB. Reject before sending to OpenEMR.
- Returns the same `(ok, ..., error, latency_ms)` tuple pattern used by FhirClient

## Acceptance criteria

- [ ] `DocumentClient` class with `upload`, `list`, `download` methods
- [ ] Magic-byte validation rejects non-PDF/PNG/JPEG files before upload
- [ ] Size validation rejects files >20MB before upload
- [ ] Unit tests with mocked HTTP: successful upload returns document_id, list returns document metadata array, download returns bytes + mimetype
- [ ] Unit tests cover error cases: 401 (unauthorized), 404 (patient not found), 413 (too large from server)
- [ ] CareTeam gate enforced before each operation (same pattern as existing tools)

## Blocked by

- `issues/001-tools-split-standard-api-client.md` (StandardApiClient provides the HTTP layer)

## User stories addressed

- User story 1 (upload lab PDF)
- User story 2 (find existing documents)
- User story 5 (upload intake form)
