## Parent PRD

`issues/prd.md`

## What to build

**A-path custom module. Only run if issue 053's spike fails any of
its four assertions.** If the spike passes, this issue is closed
unstarted in favor of issue 054.

Build the post-MVP custom OpenEMR PHP module described in
`W2_ARCHITECTURE.md` line 206: a write endpoint that lands extracted
lab values in the same tables OpenEMR's native lab pipeline uses, so
the data shows up in the chart's lab results view AND round-trips
through the FHIR read surface.

The Python side reuses the `LabResultPersister` Protocol from issue
054 (or defines it here if 054 is closed unstarted) — same call
site, different backend. Selection happens via
`FHIR_LAB_PERSISTENCE_BACKEND=openemr_module`.

See PRD §Implementation Decisions › FHIR Observation Persistence
(B → A) › Phase A execution plan and Both phases.

## Acceptance criteria

- [ ] New module under
      `interface/modules/custom_modules/oe-module-copilot-lab-writer/`
      following the existing custom-module convention used in this
      repo
- [ ] `POST /api/patient/:pid/lab_result` accepts JSON payload:
      LOINC code, value, unit, reference range, effective-datetime,
      ordering provider
- [ ] Endpoint writes into `procedure_order` and `procedure_result`
      tables matching what OpenEMR's native lab pipeline produces
- [ ] Written values surface in
      `GET /fhir/Observation?patient={pid}&category=laboratory`
      response — round-trip closed
- [ ] Writes are idempotent using patient id +
      `DocumentReference/{id}` + extraction `field_path`; re-running
      the same extraction updates/skips instead of duplicating lab
      rows
- [ ] Stored lab records preserve provenance back to the source
      `DocumentReference/{id}` in the closest OpenEMR-native field
      available, and the FHIR read surface exposes that provenance
- [ ] Unit mapping uses UCUM when possible and preserves the original
      literal unit when normalization is not safe
- [ ] Abnormal flags map to the FHIR `interpretation` values returned
      by `/fhir/Observation`
- [ ] Reference ranges parse into structured low/high/unit when safe
      and preserve the original range string otherwise
- [ ] Authentication uses the existing SMART Backend Services
      client (no new auth surface)
- [ ] `OpenEmrLabResultPersister` concrete implementation of
      `LabResultPersister` POSTs to the new endpoint
- [ ] Config flag toggles backend; default flipped to
      `openemr_module` after verification
- [ ] Pipeline integration test
      (`agent/tests/integration/test_extraction_persists_labs.py`)
      passes against the new persister
- [ ] Failed module writes return per-result structured errors and
      `persistence_status=failed`; raw document annotation can remain
      available for audit/retry but the flow cannot be reported as
      final success
- [ ] Module-level integration test against the live endpoint
      asserts the round-trip into the FHIR read surface
- [ ] `W2_ARCHITECTURE.md §3.5` and the writability table at
      ~line 266 updated to describe the A-path as the shipped FHIR
      persistence approach

## Blocked by

- Blocked by `issues/053-fhir-observation-post-spike.md`
  (only proceed if the spike's assertions fail)

## User stories addressed

Reference by number from the parent PRD:

- User story 9
- User story 11
- User story 13
- User story 17
- User story 18

## Worker F partial completion note

Implemented the custom module path and Python persister integration:

- Added `interface/modules/custom_modules/oe-module-copilot-lab-writer/` with
  route registration for `POST /api/patient/:pid/lab_result`.
- Added native table writes for `procedure_order`, `procedure_order_code`,
  `procedure_report`, and `procedure_result`, plus an idempotency map keyed by
  patient id + `DocumentReference/{id}` + extraction `field_path`.
- Added Python `LabResultPersister` protocol and `OpenEmrLabResultPersister`,
  selected by `FHIR_LAB_PERSISTENCE_BACKEND=openemr_module`.
- Wired upload-time lab persistence so failed module writes surface
  `persistence_failed` with per-result structured errors instead of final
  success.
- Added FHIR read-side provenance bridge from `procedure_result.document_id`
  to Observation `derivedFrom` when OpenEMR can resolve the source document UUID.
- Updated `W2_ARCHITECTURE.md` to describe the A-path as the persistence path.

Tests run:

- `uv run pytest -q tests/integration/test_extraction_persists_labs.py tests/test_lab_result_persister.py tests/test_upload_endpoint.py`
- `uv run ruff check src tests/test_lab_result_persister.py tests/integration/test_extraction_persists_labs.py`
- `composer phpunit-isolated -- --filter LabResultWriterTest`
- `php -l` on new module PHP files and touched FHIR/Procedure services

Blocker / remaining work:

- Live OpenEMR module install/deploy and round-trip verification were not run
  before handoff. The local code is designed to round-trip through
  `GET /fhir/Observation?patient={pid}&category=laboratory`, but this still
  needs a live module-level integration test against an installed module before
  the issue can be considered fully done.
- A full `uv run pytest -q` run was started and stopped at user request to wrap
  quickly; it had already entered the slow/live tail and shown unrelated live
  failures, so focused local checks above are the reliable result for this
  partial handoff.
