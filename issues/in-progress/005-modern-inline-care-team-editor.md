## Parent PRD

`week2-additional-requirements.md`

## What to build

Modernize the Care Team card as part of the React dashboard because its current edit workflow happens inline on the dashboard page. React should read Care Team data from the existing FHIR API, render the modern view/edit UI, and submit edits back through the PHP host route using existing OpenEMR session, CSRF, ACL, and `CareTeamService::saveCareTeam(...)` behavior.

## Acceptance criteria

- [ ] Care Team is fetched from `GET {fhirBaseUrl}/CareTeam?patient={patientUuid}` and rendered in a modern card.
- [ ] A resource-specific CareTeam adapter maps providers, related persons, facilities, roles, statuses, notes, and team metadata into dashboard DTOs.
- [ ] PHP boot config includes the edit metadata required by the inline form: users, related persons, facilities, roles, statuses, team id/name/status, and CSRF token.
- [ ] React supports view mode, edit mode, add provider row, add related person row, remove row, cancel, and submit.
- [ ] Submit posts to the dashboard PHP host route and delegates to existing `CareTeamService::saveCareTeam(...)`.
- [ ] The save path preserves OpenEMR CSRF/session/ACL checks and does not introduce a new dashboard aggregation API.
- [ ] Care Team editor tests verify edit mode, add/remove/cancel behavior, validation, and submitted payload shape.
- [ ] PHP syntax checks pass for the host route save handling.

## Blocked by

- Blocked by `issues/001-modern-dashboard-route-shell.md`
- Blocked by `issues/002-fhir-patient-header.md`

## User stories addressed

- US5: Clinician can use the dashboard's existing inline Care Team workflow in the modern UI.
- US7: The dashboard uses existing OpenEMR auth/API boundaries without replacing backend auth or APIs.
