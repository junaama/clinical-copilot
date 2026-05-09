# Patient Dashboard Migration

## Scope

This migration replaces the legacy PHP-rendered patient demographics page
(`demographics_legacy.php`) with a modern React/Vite/TypeScript single-page
clinician dashboard. The scope is one route: the default patient summary that
a clinician sees after selecting a patient. The rest of OpenEMR remains
unchanged.

The new dashboard renders six clinical card sections from live FHIR data:
Allergies, Problem List, Medications, Prescriptions, Encounter History, and
Care Team. It preserves the same patient context, session, and navigation
behavior as the legacy route. A "View Legacy Dashboard" link provides
immediate fallback to the original PHP-rendered page.

## Why React, Vite, and TypeScript

**React** was chosen because OpenEMR's legacy frontend mixes jQuery, Angular 1.8,
and inline PHP. None of those stacks provide component isolation, compile-time
type safety, or a testable render model. React's component model maps directly
to clinical card sections, each card is an isolated unit with its own data
fetch, adapter, and render tree. This makes the dashboard testable without a
running backend.

**Vite** was chosen over Webpack or Create React App for build speed and
simplicity. The Vite configuration is 40 lines. Development rebuilds are
sub-second. Production builds output a single hashed JS bundle that PHP
discovers via `glob()`. Vite's native ESM dev server eliminates the bundling
step during development, which matters when iterating on adapter logic against
live FHIR responses.

**TypeScript** was chosen because FHIR resources are deeply nested JSON
structures where a typo in a property path (`codig` vs `coding`) silently
returns `undefined`. Strict TypeScript catches these at compile time. Every
FHIR type, adapter DTO, component prop, and boot config field is typed with
`readonly` properties, enforcing immutability at the type level. The project
uses `strict: true` with no escape hatches.

## What was gained

The legacy demographics page is a single PHP file that mixes SQL queries,
HTML rendering, JavaScript event handlers, and business logic. Testing requires
a running database, an active session, and a browser. Adding a clinical card
means editing a monolithic file and hoping the change does not break adjacent
sections.

The modern dashboard separates concerns into layers:

- **Hooks** (`use-fhir-search`, `use-fhir-patient`) handle data fetching with
  cancellation and error state, independent of what is rendered.
- **Adapters** (`allergy-adapter`, `condition-adapter`, `medication-adapter`,
  `encounter-adapter`, `careteam-adapter`, `patient-adapter`) are pure
  functions that transform FHIR bundles into dashboard DTOs. They are
  testable without React, without a DOM, and without a network.
- **Components** (`AllergyCard`, `ProblemListCard`, `MedicationCard`,
  `EncounterHistoryCard`, `CareTeamCard`, `PatientHeader`) receive typed
  props and render UI. Each card uses `ClinicalCard` as a shared shell
  that handles loading, error, and empty states uniformly.

This separation produces three concrete gains:

1. **Testability.** 131 tests run in under 2 seconds without Docker, without a
   database, and without an OpenEMR instance. Adapter tests verify FHIR-to-DTO
   transformations with fixture data. Component tests verify rendering and
   interaction with mocked fetch responses.
2. **Isolation.** Adding a new clinical card requires three files (adapter,
   component, test) and one line in `App.tsx`. No existing card is affected.
3. **Type safety.** The FHIR response shape, adapter output shape, component
   prop shape, and boot config shape are all checked at compile time. A
   mismatched field name between the PHP boot config and the React consumer
   fails the build, not the patient.

## Tradeoffs

### Temporary frontend coexistence

The repository now contains two frontend stacks for the same route: the legacy
PHP-rendered page and the modern React dashboard. Both are maintained. The
legacy page is preserved at `demographics_legacy.php` and linked from the
modern dashboard header. This coexistence is intentional: it allows clinicians
to fall back immediately if the modern dashboard has a gap. The cost is that
any future change to the patient summary layout must consider whether it
applies to one or both surfaces until the legacy route is retired.

### Build pipeline complexity

The modern dashboard requires `npm run build` to produce assets that PHP can
serve. The built output lives in `public/assets/patient-dashboard/` and is
discovered by PHP via `glob()` on hashed filenames. If assets are missing, the
PHP host renders a helpful error message instead of a blank page. This adds a
build step that the legacy route did not have, but the build is fast (under 1
second), deterministic, and produces a single JS file with source maps.

### FHIR adapter maintenance

Each clinical card depends on a resource-specific adapter that knows the FHIR
R4 shape of `AllergyIntolerance`, `Condition`, `MedicationRequest`,
`Encounter`, `CareTeam`, and `Patient`. If OpenEMR changes its FHIR output,
the adapter must be updated. This is a maintenance cost, but it is localized:
each adapter is a single file with its own test suite. The alternative,
rendering raw FHIR JSON in the UI, would shift that cost to every clinician
reading the dashboard.

### PHP-backed Care Team save

The Care Team card supports inline editing. Reads use the FHIR API
(`GET /fhir/r4/CareTeam?patient={uuid}`), but writes use a PHP form POST to
`demographics.php`, which delegates to the existing `CareTeamService`. This
hybrid approach exists because OpenEMR's FHIR API does not support CareTeam
writes. The PHP handler validates CSRF tokens, sanitizes input, and uses the
Post-Redirect-Get pattern to prevent form resubmission. The cost is that the
save path is not a REST call and requires a page reload. The gain is that it
reuses the existing, tested CareTeam persistence layer without duplicating
write logic.

## API boundary

The modern dashboard reads patient data exclusively through OpenEMR's existing
FHIR R4 API:

| Resource | Endpoint |
|----------|----------|
| Patient | `GET /fhir/r4/Patient/{uuid}` |
| AllergyIntolerance | `GET /fhir/r4/AllergyIntolerance?patient={uuid}` |
| Condition | `GET /fhir/r4/Condition?patient={uuid}` |
| MedicationRequest | `GET /fhir/r4/MedicationRequest?patient={uuid}` |
| Encounter | `GET /fhir/r4/Encounter?patient={uuid}` |
| CareTeam | `GET /fhir/r4/CareTeam?patient={uuid}` |

No new API endpoints were created. No backend API was replaced.

The React app uses `patientUuid` (a FHIR-compatible UUID) as its canonical
patient identity. The PHP host resolves the internal numeric `pid` to this UUID
at page load via `PatientService::getUuid()` and injects it into the boot
configuration. All FHIR calls use the UUID. The internal `pid` is available in
the boot config for legacy navigation links but is not used by React components
for data fetching.

The CareTeam save is the one write path. It uses a PHP form POST (not a FHIR
or REST call) because no FHIR write surface exists for CareTeam in the deployed
OpenEMR image. The CareTeam edit metadata (available users, facilities, roles,
statuses, existing members) is collected by PHP at page load and injected into
the boot config, gated by ACL (`patients/demo/write`).

## Auth boundary

The modern dashboard does not implement its own authentication or session
management. It inherits the existing OpenEMR session.

**Same-origin session first.** All FHIR API calls use `credentials: 'same-origin'`
to send the browser's session cookie. The PHP host verifies the session via
`SessionWrapperFactory` before rendering the page. If the session is invalid or
expired, PHP redirects to the login page before React loads. React never sees
an unauthenticated state.

**CSRF protection.** The PHP host collects a CSRF token via `CsrfUtils` and
injects it into the boot config. The CareTeam edit form includes this token in
every POST. The PHP handler verifies it before processing the save.

**ACL gating.** The CareTeam edit UI is conditionally rendered. PHP checks
`AclMain::aclCheckCore('patients', 'demo', '', 'write')` at page load. If the
user does not have write permission, `careTeamEdit` is omitted from the boot
config, and the React component does not render the edit button.

**OAuth/OIDC fallback.** The current deployment uses same-origin session auth
because the dashboard is served by the same OpenEMR instance that provides the
FHIR API. If the dashboard were served from a separate origin (e.g., a
standalone deployment), the FHIR calls would need OAuth2 bearer tokens instead
of session cookies. The hook layer (`use-fhir-search`) would need an
`Authorization` header injection point. This path is not implemented because it
is not needed in the current deployment topology.

## UI/UX boundary

The modern dashboard preserves the clinician's existing workflow: select a
patient, see a summary of their clinical record, drill into details or edit
specific sections.

**Layout.** The dashboard renders a patient header (name, DOB, sex, MRN, active
status) followed by a grid of clinical cards. This matches the legacy page's
information hierarchy: identity at the top, clinical sections below.

**Card behavior.** Each card shows loading state, then either content, an empty
message, or an error message. Edit/add links route to the corresponding legacy
OpenEMR pages (e.g., the allergy edit page) rather than reimplementing inline
CRUD for every resource. The CareTeam card is the exception: it supports inline
editing because the existing CareTeam edit flow was already form-based and
self-contained.

**Navigation.** Patient context switching (`set_pid` query parameter) works
identically to the legacy route. The "View Legacy Dashboard" link in the
header provides immediate fallback. Legacy pages that link to the demographics
route continue to work because the URL path is unchanged.

**Accessibility.** Cards use semantic HTML (`role="alert"` for errors,
`aria-label` for interactive elements). Loading states are announced. The edit
form uses standard form controls.

## Verification

### Automated checks

1. **TypeScript compilation.** `tsc --noEmit` with `strict: true` verifies
   type correctness across all adapters, hooks, components, and boot config
   types. Runs as part of `npm run build`.

2. **Vite production build.** `vite build` produces the production bundle.
   A successful build confirms that all imports resolve, all modules
   transform, and the output is servable. Output is a single hashed JS
   file in `public/assets/patient-dashboard/assets/`.

3. **Unit and component tests.** `npm run test` runs 131 tests via Vitest:
   - 6 adapter test suites (allergy, condition, medication, encounter,
     careteam, patient) verify FHIR-to-DTO transformations with fixture data.
   - 7 component test suites (App, PatientHeader, AllergyCard, ProblemListCard,
     MedicationCard, EncounterHistoryCard, CareTeamCard) verify rendering,
     loading/error/empty states, and user interaction with mocked fetch.
   - 1 shared ClinicalCard test suite verifies the card shell.
   - 1 hook test suite verifies fetch, cancellation, and error handling.
   - All tests run without Docker, without a database, and without OpenEMR.

4. **Coverage thresholds.** The test configuration enforces 80% coverage for
   lines, functions, and statements, and 75% for branches.

5. **PHP syntax check.** `php -l demographics.php` confirms the PHP host
   file parses without syntax errors.

### Manual verification

1. **Default route.** Navigate to `demographics.php?set_pid=1` in a running
   OpenEMR instance. The modern dashboard renders with patient header and
   clinical cards populated from FHIR data.

2. **Legacy fallback.** Click "View Legacy Dashboard" in the header. The
   legacy PHP-rendered page loads at `demographics_legacy.php`.

3. **Patient switching.** Navigate to `demographics.php?set_pid=2`. The
   dashboard renders data for the new patient.

4. **CareTeam editing.** (Requires write ACL.) Click "Edit" on the Care Team
   card, modify a member, click "Save Team". The page reloads with updated
   data.

5. **Missing assets.** Delete `public/assets/patient-dashboard/` and reload.
   The page shows a "run npm run build" error instead of a blank page.

## File inventory

### New files (frontend)

| File | Purpose |
|------|---------|
| `frontend/patient-dashboard/package.json` | Dependencies and scripts |
| `frontend/patient-dashboard/vite.config.ts` | Vite + Vitest configuration |
| `frontend/patient-dashboard/tsconfig.json` | TypeScript strict configuration |
| `frontend/patient-dashboard/src/main.tsx` | React root entry point |
| `frontend/patient-dashboard/src/App.tsx` | Top-level layout with card grid |
| `frontend/patient-dashboard/src/types.ts` | Boot config and CareTeam edit types |
| `frontend/patient-dashboard/src/fhir-types.ts` | Minimal FHIR R4 type definitions |
| `frontend/patient-dashboard/src/hooks/use-fhir-search.ts` | Generic FHIR search hook |
| `frontend/patient-dashboard/src/hooks/use-fhir-patient.ts` | Patient resource hook |
| `frontend/patient-dashboard/src/adapters/*.ts` | 6 FHIR-to-DTO adapters |
| `frontend/patient-dashboard/src/components/*.tsx` | 7 UI components |
| `frontend/patient-dashboard/src/__tests__/*.ts(x)` | 15 test files |

### Modified files (PHP)

| File | Purpose |
|------|---------|
| `interface/patient_file/summary/demographics.php` | Modern dashboard host route |

### Preserved files (PHP)

| File | Purpose |
|------|---------|
| `interface/patient_file/summary/demographics_legacy.php` | Legacy fallback |
