## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Wire the extraction pipeline into three LangChain tools (`attach_document`, `list_patient_documents`, `extract_document`) and add persistence logic that writes intake-derived facts to OpenEMR and stores lab extractions in the agent's Postgres.

**Modules:**
- `agent/src/copilot/tools/extraction.py` — the 3 tools
- `agent/src/copilot/extraction/persistence.py` — write logic

**Tools:**
- `attach_document(patient_id, file_path, doc_type)` → upload via DocumentClient, return document_id
- `list_patient_documents(patient_id, category?)` → list via DocumentClient
- `extract_document(patient_id, document_id, doc_type)` → download → VLM extract → bbox match → persist → return extraction + bboxes

**Persistence:**
- Lab extractions: INSERT into `document_extractions` table (agent's Postgres) with extraction_json and bboxes_json
- Intake form: write allergies, medications, medical_problems to OpenEMR via StandardApiClient; update Patient demographics via FhirClient
- All tools enforce CareTeam gate before execution

**DB migration:** Create `document_extractions` table (id, document_id, patient_id, doc_type, extraction_json JSONB, bboxes_json JSONB, created_at)

## Acceptance criteria

- [ ] `attach_document` uploads file to OpenEMR and returns document metadata
- [ ] `list_patient_documents` returns list of documents for a patient
- [ ] `extract_document` runs full pipeline: download → VLM → schema validation → bbox matching → persistence
- [ ] Lab extraction stored in `document_extractions` table with correct JSON
- [ ] Intake extraction writes allergies/medications/medical_problems to OpenEMR
- [ ] CareTeam gate enforced on all three tools
- [ ] DB migration for `document_extractions` table
- [ ] Unit tests (mocked clients): successful extraction persisted, CareTeam rejection, invalid doc_type rejected
- [ ] Integration test: extract_document with a fixture PDF produces schema-valid output

## Blocked by

- `issues/003-openemr-document-client.md` (DocumentClient for upload/download)
- `issues/004-vlm-extraction-pipeline.md` (VLM extraction)
- `issues/005-bbox-matcher.md` (bounding box computation)

## User stories addressed

- User story 1 (upload lab PDF through Co-Pilot)
- User story 2 (find and analyze existing documents)
- User story 5 (upload intake form)
- User story 6 (intake data written to chart)
- User story 11 (low confidence flagged)
