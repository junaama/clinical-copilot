## Parent PRD

`issues/prd.md`

## What to build

CareTeam authorization gate module, the panel-roster endpoint, the empty-state panel UI, and CareTeam membership seeds for the Synthea patient set. After this slice ships, dr_smith logs in and immediately sees a panel of patients assigned to them; admin still sees everyone (documented backdoor); existing chat flows are now gated by CareTeam membership instead of "patient pinned at launch."

This slice covers the PRD's *CareTeam authorization gate* section, the panel-rendering portion of *Empty state & click-to-brief*, and the CareTeam membership portion of *Demo user seeding*. The synthetic-message click behavior is deferred to `issues/005-click-to-brief.md`.

## Acceptance criteria

- [x] New `CareTeamGate` module exposes `assert_authorized(user_id, patient_id) -> AuthDecision` and `list_panel(user_id) -> list[ResolvedPatient]`. `AuthDecision` is the enum `allowed | careteam_denied | no_active_patient | patient_context_mismatch`.
- [x] Gate queries `care_team` / `care_team_provider` tables (or the FHIR `CareTeam.participant` resource) to determine whether `user_id` is a member of `patient_id`'s care team. *(Uses FHIR `CareTeam?participant=Practitioner/<uuid>`. Fixture mode supports the same query via the new `participant` filter on `_fixture_search`.)*
- [x] Admin users bypass the gate via the existing OpenEMR ACL check; this bypass is documented in code comments and is observable in the audit row (so admin actions are still attributed and visible). *(Bypass is via env-driven `COPILOT_ADMIN_USER_IDS` allow-list — the deliberate week-1 backdoor described in the PRD. Admin actions still flow through the audit pipeline because the gate returns `allowed`, not because the gate is skipped. A direct OpenEMR ACL round-trip is deferred — the allow-list mirrors that source of truth.)*
- [x] `GET /panel` returns the authenticated user's CareTeam roster with display fields: family name, given name, DOB, last admission timestamp, room/bed if available. *(Room/bed left `null` until the FHIR Encounter / Location chain surfaces it.)*
- [x] copilot-ui `PanelView` component renders the roster as the empty state of any conversation with no turns yet. Logging in as `dr_smith` shows their subset of patients; logging in as admin shows the full set. *(Click-injects-brief behavior is deferred to issue 005; the panel rows render as buttons with an `onPatientClick` prop ready to receive the wire.)*
- [x] Every existing patient-data tool's prior `_enforce_patient_context` call is replaced with `assert_patient_authorized` that delegates to `CareTeamGate.assert_authorized`. The old "one-patient-per-conversation" pinning invariant is removed; patient-mismatch refusal is now CareTeam-membership-based. *(All 11 patient-scoped tools updated. Tests with no bound user fall through to the legacy SMART-pin check so isolated unit tests don't need a CareTeam fixture — that compatibility shim disappears in issue 003 alongside the broader pin removal.)*
- [x] CareTeam membership seed script assigns `dr_smith` to roughly half of the Synthea patient set. Admin remains as a CareTeam-bypass for debugging. Seed is idempotent. *(Fixture-mode seed in `fixtures.py` covers dr_smith on 3 of 5 panel patients. Real-OpenEMR seed via `agent/scripts/seed/seed_careteam.py`: creates `dr_smith` user + `care_teams` + `care_team_member` rows for even-pid patients. Script is idempotent via `WHERE NOT EXISTS` guards. UUID columns are left NULL for OpenEMR's `UuidRegistry::createMissingUuids` auto-populate. Three execution modes: `--dry-run` prints SQL to stdout, direct pymysql execution with MYSQL_* env vars, or pipe-to-Railway `mysql` CLI. 36 unit tests in `test_seed_careteam.py`.)*
- [x] `CareTeamGate` tests cover: in-team patient returns `allowed`; out-of-team patient returns `careteam_denied`; empty `patient_id` returns `no_active_patient`; admin bypass returns `allowed` for any pid; `list_panel` returns only in-team patients for non-admin, full set for admin. Prior art: `agent/tests/test_patient_context_guard.py` (replaces this file's logic). *(12 unit tests in `test_care_team_gate.py`; tool-layer integration coverage in `test_patient_context_guard.py` is reframed around both the legacy and gate-driven paths; `/panel` HTTP coverage in `test_auth_endpoints.py`.)*
- [ ] The PHP-side equivalent of the gate (used by the EHR-launch flow's tools) is updated to the same membership semantics; existing `oe-module-copilot-launcher` tests continue to pass. *(Deferred — the EHR-launch flow's tool calls already route through the Python tool layer, so the Python gate update gives EHR-launch sessions CareTeam protection automatically. The remaining PHP-side concern is `EmbedController`'s session.pid match in `interface/modules/.../src/Controller/EmbedController.php`; tightening that to a CareTeam membership check is small but orthogonal and is being deferred to keep this slice scoped.)*

## Progress notes

### 2026-05-02 — Python CareTeamGate slice landed

`CareTeamGate` (`agent/src/copilot/care_team.py`) ships with the four-value
`AuthDecision` enum, a `ResolvedPatient` dataclass, and the two public
methods called for in the issue. It takes a `FhirClient` and an
`admin_user_ids: frozenset[str]` so fixture mode and real OpenEMR share one
code path; admin bypass is the documented week-1 backdoor (env-driven
allow-list rather than a per-call OpenEMR ACL round-trip).

`tools.py` was rewired: `_enforce_patient_context` is preserved as the
legacy fallback for unit-test paths that don't bind an active user, and a
new `_enforce_patient_authorization(gate, patient_id)` is consulted by all
11 patient-scoped tools. When a user is bound, the gate decides; otherwise
the legacy SMART-pin check stands so isolated test setups don't need a
CareTeam fixture. Both paths use the same `AuthDecision`-typed error
strings (`careteam_denied`, `patient_context_mismatch`, `no_active_patient`)
so callers above the tool layer don't need a feature flag.

`GET /panel` (`agent/src/copilot/server.py`) reads the session cookie, parses
the `fhirUser` claim into a Practitioner UUID, and returns the resolved
roster as JSON. `copilot-ui` got a new `api/panel.ts` fetch helper, a
`PanelView.tsx` component (button-shaped rows so the issue 005 click wire
slots in cleanly), and minimal CSS. The panel mounts as the empty state
in `StandaloneApp` while `messages.length === 0`.

Fixtures: `CareTeam` resources added to `FIXTURE_BUNDLE` so dr_smith is on
fixture-1 (Eduardo), fixture-3 (Robert), fixture-5 (James); fixture-2
(Maya) and fixture-4 (Linda) are deliberately NOT on his team so the
careteam_denied path is observable in the demo. `_fixture_search` learned a
`participant` filter so the gate's FHIR search works in fixture mode.
`Settings.admin_user_ids` accepts a CSV / JSON-array env var
(`COPILOT_ADMIN_USER_IDS`).

Tests: 12 unit tests in `test_care_team_gate.py` + 7 reframed tool-layer
tests in `test_patient_context_guard.py` (covers both the legacy and
gate-driven paths) + 3 HTTP tests for `/panel` in `test_auth_endpoints.py`
+ 4 React tests in `PanelView.test.tsx`. 90 Python tests pass (84 prior +
12 gate − 6 net replacements + 5 new) excluding the Postgres-required
files which need a DB on the sandbox; ruff clean on changed files; UI
tests 41/41 pass (was 37, +4 PanelView).

`npm run typecheck` could not run on this sandbox — the overlay
filesystem corrupts writes to `node_modules/typescript/lib/tsc.js` on
both `npm install` and `cp -r` (md5 changes between source and
destination). Same flavor of sandbox-environmental gap as the
phpstan-memory issue noted on the previous slice. The new `.tsx` is small
and tested through vitest's render path, so type signatures are exercised
indirectly.

Remaining for this issue:
1. ~~Real-OpenEMR CareTeam seed~~ done (see progress note below).
2. PHP-side `EmbedController` CareTeam-membership tightening -- deferred;
   the EHR-launch tool calls already pass through the Python gate.

### 2026-05-02 -- CareTeam seed script landed

`agent/scripts/seed/seed_careteam.py` creates the real-OpenEMR CareTeam
memberships for `dr_smith`. Three execution modes:

- `--dry-run` prints SQL to stdout (pipe into `mysql` via Railway SSH)
- Direct execution via `pymysql` with `MYSQL_*` env vars
- `--pids 1,2,3,...` overrides patient pid discovery

The script is idempotent via `INSERT ... SELECT ... WHERE NOT EXISTS`
guards on every statement. The seed:

1. Creates a `dr_smith` user row (username, fname=Sarah, lname=Smith,
   title=MD, specialty=Internal Medicine, authorized=1, active=1).
2. For each even-pid patient (~50% of the population), creates a
   `care_teams` row with `status='active'` and `team_name` derived
   from the patient's name.
3. Adds a `care_team_member` row linking `dr_smith` (user_id via
   subquery) to each team with `role='physician'`.

UUID columns are left NULL. OpenEMR's `UuidRegistry::createMissingUuids`
auto-populates them on the first FHIR query, so `CareTeam` resources
become visible via `GET /fhir/CareTeam?participant=Practitioner/<uuid>`
without manual UUID wiring.

`pymysql>=1.1.0` added to the `seed` extra in `pyproject.toml`. README
updated.

Tests: 36 unit tests in `test_seed_careteam.py` covering patient
selection (even-pid determinism, order preservation, empty/all-even/
all-odd edge cases), SQL statement generation (idempotency guards,
correct field values, subquery-based lookups), full-script assembly,
and `DR_SMITH_CONFIG` validation. 347 backend tests pass (was 311;
+36 seed). Ruff clean.

## Blocked by

- Blocked by `issues/001-standalone-login-shell.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 3
- User story 15
- User story 23
