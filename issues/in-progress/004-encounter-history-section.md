## Parent PRD

`week2-additional-requirements.md`

## What to build

Add Encounter History as the required additional API-backed dashboard section. The section should fetch encounters from the existing FHIR API, adapt them into a dashboard-friendly list, and render a clinician-scannable history without introducing a new backend endpoint.

## Acceptance criteria

- [ ] Encounter history is fetched from `GET {fhirBaseUrl}/Encounter?patient={patientUuid}`.
- [ ] A resource-specific Encounter adapter maps date/period, type or reason, status, and identifier fields into a UI DTO.
- [ ] The Encounter History section renders a modern list/table consistent with the migrated dashboard layout.
- [ ] The section includes loading, empty, and error states.
- [ ] Adapter tests cover complete encounters, encounters with partial period data, and empty bundles.
- [ ] Component tests verify populated and empty Encounter History rendering.

## Blocked by

- Blocked by `issues/001-modern-dashboard-route-shell.md`
- Blocked by `issues/002-fhir-patient-header.md`

## User stories addressed

- US4: Clinician sees Encounter History as the additional API-backed section.
- US7: The dashboard uses existing OpenEMR auth/API boundaries without replacing backend auth or APIs.
