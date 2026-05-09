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

- [x] New module under
      `interface/modules/custom_modules/oe-module-copilot-lab-writer/`
      following the existing custom-module convention used in this
      repo
- [x] `POST /api/patient/:pid/lab_result` accepts JSON payload:
      LOINC code, value, unit, reference range, effective-datetime,
      ordering provider
- [x] Endpoint writes into `procedure_order` and `procedure_result`
      tables matching what OpenEMR's native lab pipeline produces
- [x] Written values surface in
      `GET /fhir/Observation?patient={pid}&category=laboratory`
      response — round-trip closed
- [x] Writes are idempotent using patient id +
      `DocumentReference/{id}` + extraction `field_path`; re-running
      the same extraction updates/skips instead of duplicating lab
      rows
- [x] Stored lab records preserve provenance back to the source
      `DocumentReference/{id}` in the closest OpenEMR-native field
      available, and the FHIR read surface exposes that provenance
- [x] Unit mapping uses UCUM when possible and preserves the original
      literal unit when normalization is not safe
- [x] Abnormal flags map to the FHIR `interpretation` values returned
      by `/fhir/Observation`
- [x] Reference ranges parse into structured low/high/unit when safe
      and preserve the original range string otherwise
- [x] Authentication uses the existing SMART Backend Services
      client (no new auth surface)
- [x] `OpenEmrLabResultPersister` concrete implementation of
      `LabResultPersister` POSTs to the new endpoint
- [x] Config flag toggles backend; default flipped to
      `openemr_module` after verification
- [x] Pipeline integration test
      (`agent/tests/integration/test_extraction_persists_labs.py`)
      passes against the new persister
- [x] Failed module writes return per-result structured errors and
      `persistence_status=failed`; raw document annotation can remain
      available for audit/retry but the flow cannot be reported as
      final success
- [x] Module-level integration test against the live endpoint
      asserts the round-trip into the FHIR read surface
- [x] `W2_ARCHITECTURE.md §3.5` and the writability table at
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

## Worker G completion note

Verified and finished issue 055 after Galileo's `c7dd97c` implementation.

Added an opt-in live module test:

- `agent/tests/integration/test_openemr_lab_result_module_live.py`
- It posts to `POST /api/patient/{pid}/lab_result`, repeats the same payload to
  assert idempotent update semantics, and reads
  `GET /fhir/Observation?patient={patient_uuid}&category=laboratory` to assert
  the value, UCUM unit, high interpretation, structured reference range, and
  `derivedFrom` provenance.

Local live OpenEMR evidence from the running `docker/development-easy` stack:

- Installed/enabled `oe-module-copilot-lab-writer` in the local OpenEMR
  database and ran `sql/install.sql`.
- Registered a local OpenEMR API test client and generated a bearer token with
  `api:oemr`, `api:fhir`, `user/lab_result.c`, and read scopes.
- Posted payload for patient pid `1`, document `DocumentReference/1453`,
  field path `live_verification.glucose_20260509132621`.
- Write response: HTTP 200,
  `persistence_status=succeeded`, `persistence_status=created`,
  `procedure_order_id=4100`, `procedure_report_id=373`,
  `procedure_result_id=7662`.
- FHIR read-back with patient UUID `a1a7005d-850b-4567-b1e3-6f940ed71ead`:
  HTTP 200 Bundle with one matching Observation containing LOINC `2345-7`,
  value `123 mg/dL`, UCUM system `http://unitsofmeasure.org`,
  interpretation `H`, reference range low `70` high `99`, and
  `derivedFrom=DocumentReference/a1b97432-4c18-4046-9313-2cd4830b741d`.
- Re-posted the identical payload: HTTP 200,
  `persistence_status=succeeded`, result status `updated`, same
  `procedure_result_id=7662`, and `copilot_lab_result_map` count remained `1`.

Verification run:

- `uv run pytest -q tests/integration/test_extraction_persists_labs.py tests/test_lab_result_persister.py tests/test_upload_endpoint.py`:
  24 passed.
- `uv run ruff check src tests/test_lab_result_persister.py tests/integration/test_extraction_persists_labs.py tests/test_upload_endpoint.py`:
  passed.
- `composer phpunit-isolated -- --filter LabResultWriterTest`: 3 passed,
  22 assertions.
- `php -l` on the custom module PHP files,
  `src/Services/FHIR/Observation/FhirObservationLaboratoryService.php`, and
  `src/Services/ProcedureService.php`: passed.
- `uv run pytest -q -m 'not live and not live_http'`: 1096 passed,
  29 skipped, 9 deselected, 24 failed. Failures were existing W2
  smoke/golden/adversarial eval gate failures, not lab-writer module failures.
- `OPENEMR_LAB_WRITER_TOKEN=... OPENEMR_LAB_WRITER_PATIENT_PID=1
  OPENEMR_LAB_WRITER_PATIENT_FHIR_ID=a1a7005d-850b-4567-b1e3-6f940ed71ead
  OPENEMR_LAB_WRITER_DOCUMENT_ID=1453 uv run pytest -m live -q
  tests/integration/test_openemr_lab_result_module_live.py`: 1 passed.
- `uv run ruff check src tests/test_lab_result_persister.py
  tests/integration/test_extraction_persists_labs.py tests/test_upload_endpoint.py
  tests/integration/test_openemr_lab_result_module_live.py`: passed.
- `uv run pytest -q tests/integration/test_extraction_persists_labs.py
  tests/test_lab_result_persister.py tests/test_upload_endpoint.py
  tests/integration/test_openemr_lab_result_module_live.py`: 24 passed,
  1 deselected, 5 warnings.

Disposition: complete. Live local OpenEMR module install, write, idempotent
repeat, and FHIR Observation round-trip were verified.
