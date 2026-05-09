## Parent PRD

`week2-additional-requirements.md`

## What to build

Render the required clinical cards in the modern dashboard from live OpenEMR FHIR data while preserving existing route-out edit actions. The cards are Allergies, Problem List, Medications, and Prescriptions. Each card should use a resource-specific adapter so UI components do not depend on raw FHIR resource shape.

## Acceptance criteria

- [ ] Allergies are fetched from `GET {fhirBaseUrl}/AllergyIntolerance?patient={patientUuid}` and rendered in a modern card.
- [ ] Problem List entries are fetched from `GET {fhirBaseUrl}/Condition?patient={patientUuid}` and rendered in a modern card.
- [ ] Medications and prescriptions are fetched from existing OpenEMR FHIR medication endpoints, prioritizing `MedicationRequest?patient={patientUuid}` for prescription-style requests.
- [ ] Resource-specific adapters convert FHIR AllergyIntolerance, Condition, and MedicationRequest resources into dashboard DTOs.
- [ ] Each card includes loading, empty, and error states.
- [ ] Existing edit/add actions route to the corresponding legacy OpenEMR pages rather than being reimplemented inline.
- [ ] Adapter tests cover active records, inactive/resolved records where applicable, partial FHIR resources, and empty bundles.
- [ ] Component tests verify populated and empty card rendering.

## Blocked by

- Blocked by `issues/001-modern-dashboard-route-shell.md`
- Blocked by `issues/002-fhir-patient-header.md`

## User stories addressed

- US3: Clinician sees required clinical cards from live FHIR data.
- US7: The dashboard uses existing OpenEMR auth/API boundaries without replacing backend auth or APIs.
