# Architecture Audit — Raw Findings

Citations are absolute paths inside `/Users/macbook/dev/Gauntlet/week1/openemragent/`; the prefix is omitted in the prose for brevity.

## High-level diagram (ASCII)

```
                              Browser (clinician / staff)
                                       │
                                       │ HTTPS
                                       ▼
                ┌──────────────────────────────────────────────┐
                │        Apache + mod_php (PHP 8.2+)           │
                │  (production: openemr/openemr image)         │
                └─────┬───────────────────────┬────────────────┘
                      │                       │
       index.php → interface/login/login.php  │
       /interface/main/tabs/main.php          │ /apis/  /oauth2/  /portal/  /fhir/
       (Knockout/jQuery + Bootstrap UI,       │
        Smarty/Twig server templates)         ▼
                      │        ┌────────────────────────────────────┐
                      │        │   apis/dispatch.php                │
                      │        │   → ApiApplication (Symfony HttpKernel)
                      │        │   → Subscribers: SiteSetup, CORS,  │
                      │        │     OAuth2, Authorization, Routes, │
                      │        │     ViewRenderer, Telemetry, ...   │
                      │        └─────────────┬──────────────────────┘
                      │                      │
                      │                      ▼
                      │        ┌────────────────────────────────────┐
                      │        │ src/RestControllers/*RestController│
                      │        │ src/RestControllers/FHIR/*         │
                      │        │ src/RestControllers/SMART/*        │
                      │        └─────────────┬──────────────────────┘
                      │                      │
                      ▼                      ▼
        ┌─────────────────────────────────────────────────────────┐
        │           src/Services/*Service (BaseService)           │
        │  PatientService, EncounterService, ListService,         │
        │  FHIR\Fhir*Service, EventDispatcher hooks               │
        └─────────────────────┬───────────────────────────────────┘
                              │
                ┌─────────────┴────────────────┐
                ▼                              ▼
   library/sql.inc.php (sqlQuery,    src/BC/DatabaseConnectionFactory
   sqlStatement, sqlInsert)              (ADODB + Doctrine DBAL +
   wraps QueryUtils + ADODB_mysqli_log    raw mysqli)
                              │
                              ▼
                ┌─────────────────────────────┐
                │   MariaDB 11.8 / MySQL      │
                │   281 tables (database.sql) │
                └─────────────┬───────────────┘
                              │ separate connection for audit
                              ▼
                ┌─────────────────────────────┐
                │ log / audit_master /        │
                │ audit_details / api_log /   │  ← optional ATNA TCP
                │ extended_log                │     audit_master mirror
                └─────────────────────────────┘

Side services (Docker compose):
  - phpMyAdmin (8310)   - selenium (4444 grid)   - couchdb (6984)
  - openldap            - mailpit                - redis (variant only)
```

## Layering: src vs library vs interface vs apis vs modules

`src/` (PSR-4 `OpenEMR\` namespace) is the modern code: roughly **579,000 lines across 1,942 PHP files**, but FHIR R4 generated stubs dominate the count (the largest single file is `src/FHIR/R4/PHPFHIRParserMap.php` at 61,474 lines). The substantive modern code is in `src/Services/`, `src/RestControllers/`, `src/Common/`, `src/Core/`, and `src/Events/`. Representative files: `src/Services/PatientService.php` (1,007 lines, a `BaseService` subclass), `src/Common/Database/QueryUtils.php`, `src/RestControllers/ApiApplication.php`, `src/Core/Kernel.php`, `src/Common/Logging/EventAuditLogger.php`.

`library/` is legacy procedural PHP: ~**102,000 lines across 596 files**, almost all top-level functions plus a few classes under `library/classes/`. The biggest legacy files are `library/options.inc.php` (4,869 lines), `library/globals.inc.php` (4,583 lines), `library/clinical_rules.php` (3,532 lines), `library/patient.inc.php` (1,703 lines), and `library/FeeSheet.class.php` (1,618 lines). `library/sql.inc.php` (file:1) is the single most important legacy entry-point: every DB call from the legacy world goes through `sqlQuery`/`sqlStatement`/`sqlInsert` defined here. Ratio: src/library is roughly 5.7:1 by LOC, but if FHIR generated stubs are excluded the ratio collapses to roughly 2:1, with `library/` carrying the majority of *business logic* still in production paths.

`interface/` (~**276,000 lines across 1,001 files**) is the legacy web UI. Each subdirectory is effectively a feature: `interface/login/login.php` (browser auth flow), `interface/main/main_screen.php` (top frame, file:1) and `interface/main/tabs/main.php` (the actual SPA shell that renders the tab system, file:455 `<body>`), `interface/patient_file/summary/demographics.php` (patient summary cards), `interface/patient_file/encounter/encounter_top.php` (encounter view), `interface/forms/clinical_notes/new.php` (one of dozens of "form" plugins). Routing is **filesystem-based** — Apache rewrites map URLs directly to PHP files, and each PHP file is responsible for its own auth check (typically by `require_once 'interface/globals.php'` which runs the session and auth pipeline). The new `.htaccess.example` (file:13-37) shows an opt-in front controller (`public/index.php`) that routes everything through `OpenEMR\BC\FallbackRouter::performLegacyRouting` (`src/BC/FallbackRouter.php:77`); this is "experimental/opt-in" per its own comments.

`apis/` is the REST/FHIR/portal entry layer, but it is *not* a parallel codebase to `src/RestControllers/`. The flow is: Apache → `apis/dispatch.php` (file:29) → `OpenEMR\RestControllers\ApiApplication::run` → Symfony `HttpKernel` → match URL against route maps in `apis/routes/_rest_routes_standard.inc.php` (717 lines), `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php` (876 lines), or `apis/routes/_rest_routes_portal.inc.php` (49 lines). Each route is a closure that calls a controller in `src/RestControllers/`. So `apis/` is just the route table; `src/RestControllers/` is the controllers. `_rest_routes.inc.php` (top-level, file:32-36) is the manifest that tells `ApiApplication` which three route files to load.

`modules/` does not exist at the top level; module code lives at `interface/modules/custom_modules/*` (Symfony-style, e.g. `oe-module-weno/openemr.bootstrap.php` file:26 instantiates a `Bootstrap` class that calls `subscribeToEvents()` on the kernel dispatcher) and `interface/modules/zend_modules/module/*` (Laminas/Zend MVC modules, e.g. `Carecoordination/Module.php`). `OpenEMR\Core\ModulesApplication::__construct` (`src/Core/ModulesApplication.php:41`) loads both: it boots the Laminas service manager, calls `bootstrapCustomModules` which scans `interface/modules/custom_modules/` for `openemr.bootstrap.php` files (constant `CUSTOM_MODULE_BOOSTRAP_NAME`, file:39). Active modules are gated by a row in the `modules` table (`mod_active = 1`, file:99). This is the canonical extension point: a third-party module is a directory with `openemr.bootstrap.php` plus a Composer-style `src/`, registered in the `modules` table.

## Data Layer

### Database connection lifecycle

There is exactly one entry point that opens the application DB connection: `library/sql.inc.php` is `require_once`d by `interface/globals.php` (which every legacy page includes) and at lines 59-63 it calls `OpenEMR\BC\DatabaseConnectionFactory::createAdodb` and stashes the connection at `$GLOBALS['adodb']['db']` and `$GLOBALS['dbh']`. `DatabaseConnectionFactory` (`src/BC/DatabaseConnectionFactory.php:24`) has three factories: `createAdodb` (legacy ADODB binding, the default), `createDbal` (Doctrine DBAL — used by audit logging and migrations), and `createMysqli` (raw mysqli for special paths). Connections share `DatabaseConnectionOptions::forSite($site)` for credentials but are *not* pooled — `DatabaseConnectionFactory::detectConnectionPersistenceFromGlobalState` (file:155) only enables `mysqli_pconnect` when an env/global flag is set.

The legacy ADODB connection is wrapped by `ADODB_mysqli_log` (`library/ADODB_mysqli_log.php:17`). Every `Execute()` call (file:26-53) goes through `EventAuditLogger::auditSQLEvent` unless `$skipAuditLog` is set or the call uses `ExecuteNoLog` (file:64). This is why every `sqlStatement`/`sqlQuery` automatically generates a row in the `log` table — auditing is not a separate concern, it is wired into the connection wrapper.

`library/sql.inc.php` exposes the public surface used by all legacy code: `sqlStatement` (file:96, returns recordset), `sqlQuery` (file:259, single row), `sqlInsert` (file:239, returns insert id), `sqlStatementNoLog`/`sqlQueryNoLog` (audit-bypassing variants), `sqlBeginTrans`/`sqlCommitTrans`/`sqlRollbackTrans` (file:476-497), and the deprecated `privQuery`/`privStatement` (file:520-552, formerly used a separate "privileged" connection — now just delegates to the main one). All of these now delegate to `OpenEMR\Common\Database\QueryUtils` (`src/Common/Database/QueryUtils.php:22`), which is the modern API. New code is expected to call `QueryUtils::fetchRecords`, `QueryUtils::sqlStatementThrowException`, etc. directly.

The audit logger keeps a *second*, completely separate Doctrine DBAL connection (`src/Common/Logging/EventAuditLogger.php:46`, comment: "this connection must be separate from the main application connection") so that audit writes survive even if the main connection's transaction is rolled back.

### Table groups (281 total)

The list comes from `sql/database.sql` — `grep -c '^CREATE TABLE'` returns 281. Grouping by purpose:

- **Identity / access control:** `users`, `users_secure`, `users_facility`, `groups`, `gacl_*` (16 tables — phpGACL ACL system: `gacl_acl`, `gacl_aco`, `gacl_aro`, plus `_sections`, `_seq`, `_map` variants), `module_acl_group_settings`, `module_acl_user_settings`, `module_acl_sections`, `login_mfa_registrations`, `oauth_clients`, `oauth_trusted_user`, `api_token`, `api_refresh_token`, `jwt_grant_history`, `onetime_auth`, `verify_email`, `keys`.
- **Patient PHI (core demographics + history):** `patient_data`, `history_data`, `patient_history`, `patient_access_onsite`, `patient_settings`, `patient_birthday_alert`, `patient_care_experience_preferences`, `patient_treatment_intervention_preferences`, `patient_reminders`, `patient_tracker`, `patient_tracker_element`, `person`, `person_patient_link`, `lists`, `lists_medication`, `lists_touch`, `pnotes`, `onotes`, `notes`, `amendments`, `amendments_history`, `addresses`, `phone_numbers`, `contact`, `contact_relation`, `contact_telecom`, `employer_data`, `insurance_data` (in `sql/database.sql` but not shown above), `documents`, `documents_legal_*`, `categories`, `categories_to_documents`.
- **Clinical (encounters, forms, immunizations, procedures, prescriptions):** `forms`, `form_encounter`, `form_soap`, `form_vitals`, `form_vital_details`, `form_clinical_notes`, `form_clinical_instructions`, `form_care_plan`, `form_history_sdoh`, `form_dictation`, `form_eye_*` (15 ophthalmology tables), `form_ros`, `form_reviewofs`, `form_observation`, `form_questionnaire_assessments`, `form_misc_billing_options`, `form_taskman`, `form_groups_encounter`, `form_group_attendance`, `external_encounters`, `external_procedures`, `immunizations`, `immunization_observation`, `procedure_order`, `procedure_order_code`, `procedure_order_relationships`, `procedure_providers`, `procedure_questions`, `procedure_answers`, `procedure_report`, `procedure_result`, `procedure_specimen`, `procedure_type`, `prescriptions`, `clinical_notes_procedure_results`, `clinical_plans`, `clinical_plans_rules`, `clinical_rules`, `clinical_rules_log`, `rule_action`, `rule_action_item`, `rule_filter`, `rule_patient_data`, `rule_reminder`, `rule_target`, `care_team_member`, `care_teams`, `transactions`.
- **Billing / insurance:** `billing`, `claims`, `payments`, `payment_processing_audit`, `payment_gateway_details`, `prices`, `fee_schedule`, `fee_sheet_options`, `enc_category_map`, `eligibility_verification`, `benefit_eligibility`, `drug_inventory`, `drug_sales`, `drug_templates`, `drugs`, `pharmacies`, `x12_partners`, `x12_remote_tracker`, `edi_sequences`, `claimrev` (via module).
- **Audit / logging:** `log`, `log_comment_encrypt`, `audit_master`, `audit_details`, `api_log`, `extended_log`, `track_events`, `clinical_rules_log`, `direct_message_log`, `notification_log`, `erx_rx_log`, `session_tracker`.
- **System / config / templates:** `globals`, `list_options`, `customlists`, `layout_options`, `layout_group_properties`, `lbf_data`, `lbt_data`, `migrations`, `modules`, `modules_hooks_settings`, `modules_settings`, `module_configuration`, `openemr_modules`, `openemr_module_vars`, `automatic_notification`, `notification_settings`, `lang_*` (5 tables), `categories_seq`, `sequences`, `registry`, `template_users`, `document_templates`, `document_template_profiles`, `dsi_source_attributes` (Decision Support Intervention metadata).
- **Code systems / vocabularies:** `codes`, `codes_history`, `icd9_*` (4 tables), `icd10_*` (8 tables: `icd10_dx_order_code`, `icd10_pcs_order_code`, `icd10_gem_*`, `icd10_reimbr_*`), and the runtime-loaded SNOMED, RxNorm, LOINC, CVX tables (declared in upgrade SQL, populated via `library/standard_tables_capture.inc.php`; only `cvx_codes.sql` ships in `sql/`). Also `valueset`, `valueset_oid`, `standardized_tables_track`, `supported_external_dataloads`.
- **Calendar / scheduling:** `openemr_postcalendar_events`, `openemr_postcalendar_categories`, `openemr_postcalendar_categories` (with extras), `dated_reminders`, `dated_reminders_link`, `medex_outgoing`, `medex_recalls`, `medex_prefs`, `medex_icons`, `email_queue`, `onsite_documents`, `onsite_mail`, `onsite_messages`, `onsite_online`, `onsite_portal_activity`, `onsite_signatures`.

The `extended_log` table is OpenEMR's catch-all for non-PHI events; the `log` table is the HIPAA audit log; `audit_master`/`audit_details` are the ATNA-shaped audit. `api_log` records every REST call.

### Migrations

Two parallel mechanisms exist. The legacy upgrade path is per-version SQL files in `sql/` (e.g. `8_0_0-to-8_1_0_upgrade.sql`, `8_1_0-to-8_1_1_upgrade.sql`), executed by `library/sql_upgrade_fx.php` driven from `sql_upgrade.php` at the project root. There are 35+ such files going back to `2_6_0`. The modern path is **Doctrine Migrations**, configured at `db/migration-config.php:19` with the `migrations` table for state tracking and `db/Migrations/` for migration classes. Currently only one migration exists (`Version00000000000000.php` — the baseline). `db/Migrations/README.md` introduces a `CreateTableTrait` because Doctrine's diff-based default would slow down over time. New schema changes should use Doctrine; `database.sql` is the seed for fresh installs.

## Auth and Session

### Browser session flow

A browser hitting the root runs `index.php` (file:24-30): it picks a site (defaults to `default`), `require_once`s `sites/$site_id/sqlconf.php` to test installation state, and then redirects to `interface/login/login.php`. `login.php` (`interface/login/login.php:43-56`) loads composer, builds an `OEGlobalsBag`, calls `SessionUtil::setAppCookie(SessionUtil::CORE_SESSION_ID)`, and sets `$ignoreAuth = true` before pulling in `interface/globals.php`. Form submission lands on a page that includes `library/auth.inc.php`. `library/auth.inc.php:38-74` is the actual login dispatcher: when `$_GET['auth'] === 'login'` and the form contains `authUser`/`clearPass`, it constructs `new AuthUtils('login')` and calls `confirmPassword`. On success the session is hydrated with `authUser`, `authUserID`, `authProvider`, `language_choice`, etc. (handled inside `AuthUtils`). Every subsequent legacy page does `require_once 'interface/globals.php'` which `require`s `library/auth.inc.php`, which (file:97) calls `AuthUtils::authCheckSession()` — if false, the session is destroyed and the user is redirected.

`AuthUtils` (`src/Common/Auth/AuthUtils.php:56-73`) is the central authenticator and runs in one of four modes: `login`, `api`, `portal-api`, `other`. It supports password (with `AuthHash` + bcrypt), LDAP (`OPENEMR_SETTING_gbl_ldap_*`), Google sign-in (`AuthUtils::verifyGoogleSignIn`, file:59 in auth.inc.php), and MFA (`MfaUtils.php`, U2F, TOTP).

The "post-login" landing page is `interface/main/main_screen.php` → `interface/main/tabs/main.php`. `tabs/main.php:79-87` enforces a CSRF-style `token_main` match between session and query string before rendering — if mismatched, `authCloseSession()` and back to login.

### OAuth2 / API flow

REST and FHIR auth go through `oauth2/authorize.php` (file:23-26): same `HttpRestRequest::createFromGlobals` → `ApiApplication::run` pipeline as `apis/dispatch.php`. The OAuth2 server is implemented in `src/RestControllers/AuthorizationController.php` (1,890 lines — the heaviest file in the auth subsystem) using `league/oauth2-server`. Endpoints: `/oauth2/{site}/authorize`, `/oauth2/{site}/token`, `/oauth2/{site}/introspect`, `/oauth2/{site}/registration`, plus `/oauth2/{site}/.well-known/openid-configuration` (handled by `OAuth2DiscoveryController`).

The actual per-request auth is enforced by two listeners attached to the `HttpKernel` event dispatcher in `ApiApplication::run` (`src/RestControllers/ApiApplication.php:93-97`). `OAuth2AuthorizationListener` (`src/RestControllers/Subscriber/OAuth2AuthorizationListener.php:73-85`) handles OAuth2 endpoint requests themselves. `AuthorizationListener` (`src/RestControllers/Subscriber/AuthorizationListener.php:86-110`) iterates a chain of strategies on `kernel.request`: `LocalApiAuthorizationController` (for in-process calls), then `SkipAuthorizationStrategy` (for `/fhir/metadata`, `/fhir/.well-known/smart-configuration`, `/api/version`, `/api/product`), then `BearerTokenAuthorizationStrategy` which validates a JWT against the public key from `ServerConfig::getPublicRestKey()`. After token validation, scopes are populated into `OEGlobalsBag` under `oauth_scopes`.

Each route closure also calls `RestConfig::request_authorization_check($request, $section, $value)` (`src/RestControllers/Config/RestConfig.php:180`). This is the second PEP — it pulls `authUser` from the request session and runs `AclMain::aclCheckCore` against phpGACL ACLs. So an authenticated request still needs a valid ACL section/value pair (e.g. `"patients"`, `"demo"` to read demographics). Scope-level checks use a separate `RestConfig::scope_check` (file:201) for fine-grained `user/Patient.read`-style FHIR scopes.

### Legacy session vs Symfony session

`$_SESSION` is read/written in legacy code, but `SessionWrapperFactory::getInstance()->getActiveSession()` (used in `library/sql.inc.php:30`, `library/auth.inc.php:25`, etc.) returns a Symfony `SessionInterface` that wraps the same underlying storage. Two cookies/session IDs exist: `SessionUtil::CORE_SESSION_ID` (clinician/staff main app — see `interface/main/tabs/main.php:29`) and a separate portal session for `portal/`. Session storage adapters live in `src/Common/Session/Storage/`; `Predis` is one option for Redis-backed sessions (used by `docker/development-easy-redis/`). Token CSRF uses `CsrfUtils::collectCsrfToken($session)` and a separate `'api'` token for LocalApi calls (`tabs/main.php:132-134`).

### "Current user" representations

There is no single "current user" object — the codebase carries the identity in several forms simultaneously:

- `$_SESSION['authUser']` / `$session->get('authUser')` — the username string.
- `$_SESSION['authUserID']` — integer `users.id`. `PatientService::databaseInsert` reads this for `created_by` (`src/Services/PatientService.php:180`).
- `OpenEMR\Services\UserService` — modern read interface for users (`src/Services/UserService.php`), backed by the `users` table.
- `OpenEMR\Common\Auth\AuthUtils` — short-lived; populates session on login.
- `OpenEMR\Common\Auth\UuidUserAccount` — wraps a UUID for OAuth2/SMART contexts.
- `HttpRestRequest::getRequestUserId` / `getRequestUserUuid` / `isPatientRequest` / `getPatientUUIDString` (used at `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:583, 587, 611-616`) — the request-scoped view of "who is calling".

For an agent, the canonical "I'm acting as user X" data comes from `$session->get('authUser')` (browser context) or `HttpRestRequest::getRequestUserUuid()` plus the OAuth2 scopes in `OEGlobalsBag::get('oauth_scopes')` (API context).

## API Surfaces

### REST (src/RestControllers/)

The non-FHIR REST API lives at `/apis/{site}/api/...`. Routes in `apis/routes/_rest_routes_standard.inc.php` map URL patterns to closures that call controllers in `src/RestControllers/`. Each controller is hand-rolled (no auto-CRUD), typically thin: parses input, calls a `*Service`, returns the result via `RestControllerHelper::handleProcessingResult`. Representative endpoints (cited line numbers in `apis/routes/_rest_routes_standard.inc.php`):

- `GET /api/patient` (file:76-82) → `RestConfig::request_authorization_check($request, "patients", "demo")` then `(new PatientRestController())->getAll($request, $request->query->all(), $config)`. Search uses `SearchQueryConfig::createConfigFromQueryParams` for FHIR-style query parameters.
- `GET /api/patient/:puuid` (file:99-104) → ACL `patients/demo`, `PatientRestController::getOne`.
- `GET /api/patient/:puuid/encounter` (file:105-110) → ACL `encounters/auth_a`, `EncounterRestController::getAll($puuid)`.
- `GET /api/patient/:pid/medication` (file:298-303) → ACL `patients/med`, `ListRestController::getAll($pid, "medication")` — medications are stored in the generic `lists` table, not a dedicated table.
- `GET /api/patient/:pid/encounter/:eid/soap_note` (file:133-138) and the matching `POST` (file:173-178) — SOAP notes by encounter.

Authorization model: every REST closure carries an explicit ACL pair (`section`, `value`) plus the OAuth2 bearer scope check happens earlier in `AuthorizationListener`. There are about 200 routes in this file.

### FHIR R4

FHIR R4 routes live in `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php` (876 lines). Same pipeline as REST. Controllers in `src/RestControllers/FHIR/Fhir*RestController.php` (40+ controllers, one per resource). Each delegates to a service in `src/Services/FHIR/Fhir*Service.php` that extends `FhirServiceBase` and exposes `parseOpenEMRRecord` / `parseFhirResource` to convert between OpenEMR's internal row shapes and FHIR R4 resources. `FhirPatientService::parseOpenEMRRecord` (`src/Services/FHIR/FhirPatientService.php:195`) is the canonical example — it reads a `patient_data` row plus joined `history_data`/`addresses`/`patient_communication` and returns a `FHIRPatient` from `src/FHIR/R4/FHIRDomainResource/FHIRPatient`.

The route pattern at `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:578-595` (`GET /fhir/Patient`) shows the patient-binding logic: if `$request->isPatientRequest()` (i.e. the access token is patient-scoped), `_id` is forced to the bound patient's UUID — the request cannot read other patients regardless of ACLs. `getOne` (file:610-625) does the same UUID equality check and throws `AccessDeniedException` on mismatch.

US Core 3.1.0 + 7.0.0 profiles are baked into each service via `VersionedProfileTrait`. Bulk export ($export) is supported per-resource via `BulkExportSupportAllOperationsTrait` (`src/Services/FHIR/FhirPatientService.php:50-52`).

### SMART on FHIR

`src/RestControllers/SMART/`: four controllers — `SMARTAuthorizationController` (handles the SMART launch dance / patient picker), `SMARTConfigurationController` (returns `/fhir/.well-known/smart-configuration`), `ScopePermissionParser` (parses `patient/Patient.read`-style scopes), and `PatientContextSearchController` (the EHR-launch patient picker). `SMARTConfigurationController::getConfig` (`src/RestControllers/SMART/SMARTConfigurationController.php:45-108`) returns the discovery document including `authorization_endpoint`, `token_endpoint`, `introspection_endpoint`, `code_challenge_methods_supported: ['S256']`, supported scopes (computed by `ScopeRepository::getCurrentSmartScopes`), and the SMART capabilities array. PKCE is supported. SMART standalone launch and EHR launch both work; `Capability::SUPPORTED_CAPABILITIES` enumerates what's claimed.

Status: production-ready for US Core 3.1.0 / 7.0.0 + SMART v1 and v2 — Inferno tests run in CI (`ci/inferno/compose.yml`).

### Internal AJAX endpoints

`library/ajax/` has 42 procedural PHP files used directly by jQuery `$.ajax` calls from the legacy UI. Examples: `library/ajax/set_pt.php` (sets the active patient in session, called from `interface/main/tabs/main.php:177`), `library/ajax/dated_reminders_counter.php` (polled every 60 seconds for reminder counts, called from `tabs/main.php:209-216`), `library/ajax/track_events.php`, `library/ajax/person_search_ajax.php`, `library/ajax/adminacl_ajax.php`. None of these go through `ApiApplication`; they `require_once 'interface/globals.php'` and rely on session-based auth. CSRF tokens are passed as `csrf_token_form` query/POST values. Modules also expose AJAX under `interface/modules/custom_modules/{module}/...`. An agent reaching these would need to be in the session context (cookie auth) and pass the `csrf_token_form` matching the session token.

## Event System

`src/Events/` contains 79 PHP files. The dispatcher is Symfony's `EventDispatcher`, accessed via `OEGlobalsBag::getInstance()->getKernel()->getEventDispatcher()` from anywhere in the app (e.g. `src/Services/PatientService.php:189, 201, 268, 291`). Each event class declares an `EVENT_HANDLE` constant string used as the dispatcher key.

Events that matter for an agent:

- **Patient lifecycle:** `PatientCreatedEvent` (`src/Events/Patient/PatientCreatedEvent.php:27` — `'patient.created'`), `BeforePatientCreatedEvent` (mutable, lets listeners alter the data before insert — `src/Services/PatientService.php:189-190` reads `$beforePatientCreatedEvent->getPatientData()` back), `PatientUpdatedEvent` (carries both old and new data, dispatched at `PatientService.php:291`), `BeforePatientUpdatedEvent`. All four are dispatched from `PatientService::databaseInsert`/`databaseUpdate`.
- **Encounter:** `EncounterMenuEvent`, `EncounterButtonEvent`, `EncounterFormsListRenderEvent`, `LoadEncounterFormFilterEvent` — these are mostly UI extension hooks (where to render a button, how to filter the form list).
- **Appointments:** `AppointmentSetEvent`, `AppointmentRenderEvent`, `AppointmentDialogCloseEvent`, `AppointmentsFilterEvent`, `CalendarFilterEvent`, `CalendarUserGetEventsFilter`.
- **Cards / UI:** `Patient\Summary\Card\RenderEvent` (`src/Events/Patient/Summary/Card/RenderEvent.php:24` — `'patientSummaryCard.render'`) is the cleanest insertion point for adding UI to the patient dashboard; listeners can call `addAppendedData(RenderInterface $object)` or `addPrependedData` on each card render. `Main\Tabs\RenderEvent` has `EVENT_BODY_RENDER_PRE` and `EVENT_BODY_RENDER_POST` (`src/Events/Main/Tabs/RenderEvent.php:20-22`) — fires at `<body>` start/end of `interface/main/tabs/main.php:460`.
- **REST API extension:** `RestApiCreateEvent` (`src/Events/RestApiExtend/RestApiCreateEvent.php:10` — `'restConfig.route_map.create'`) is dispatched once per request by the REST routing layer with `$route_map`, `$fhir_route_map`, `$portal_route_map` exposed via `addToRouteMap($route, $action)` (file:50). This is how custom modules add REST routes at runtime. `RestApiSecurityCheckEvent` (referenced at `src/RestControllers/Subscriber/AuthorizationListener.php:44`) lets modules add their own auth checks.
- **User:** `UserCreatedEvent`, `UserUpdatedEvent`, `UserEditRenderEvent`.
- **CDA:** `CDAPreParseEvent`, `CDAPostParseEvent` for clinical document import.
- **Module loading:** `ModuleLoadEvents` constants (`src/Core/ModulesApplication.php:23`).

There is no `PrescriptionCreatedEvent`, no encounter-write event, no medication-prescribed event in the modern event system — for those, you'd hook the underlying service or REST/FHIR write through other means.

A custom module subscribing to events: see `interface/modules/custom_modules/oe-module-weno/openemr.bootstrap.php:26-27` — `new Bootstrap($eventDispatcher); $bootstrap->subscribeToEvents()`. The `Bootstrap` class (in the module's `src/`) calls `$eventDispatcher->addListener(EventName::EVENT_HANDLE, [$this, 'handler'])` for each event of interest.

## Logging

Two parallel systems with different sinks.

`OpenEMR\Common\Logging\SystemLogger` (`src/Common/Logging/SystemLogger.php:30-57`) is the developer/operational PSR-3 logger. It wraps Monolog; the underlying handler is `Monolog\Handler\ErrorLogHandler` writing to PHP's `error_log` (typically `/var/log/apache2/error.log` in the Docker images). Log level is `WARNING` by default, `DEBUG` when the `system_error_logging` global is set to `'DEBUG'`. Available app-wide via `ServiceContainer::getLogger()` or the `SystemLoggerAwareTrait`.

`OpenEMR\Common\Logging\EventAuditLogger` (`src/Common/Logging/EventAuditLogger.php:34`) is the HIPAA-compliance audit logger. It uses a singleton (`SingletonTrait`), opens a *separate* Doctrine DBAL connection, and pushes events through one or more `SinkInterface` implementations. Two sinks ship: `LogTablesSink` (`src/Common/Logging/Audit/LogTablesSink.php`) writes to the `log` and `audit_master`/`audit_details` tables; `AtnaSink` (`src/Common/Logging/Audit/AtnaSink.php:18`) emits RFC 3881 XML over a TLS-secured TCP socket via `Atna/TcpWriter` when `enable_atna_audit` is set. The audit logger is invoked automatically by the ADODB wrapper for every SQL statement (`library/ADODB_mysqli_log.php:50`), by `library/auth.inc.php:88-90` for logout events, and explicitly throughout the codebase via `EventAuditLogger::getInstance()->newEvent($eventType, $user, $provider, $success, $comment)`. The `extended_log` table is a separate non-PHI extension log used by some modules.

API-specific: `ApiResponseLoggerListener` (`src/RestControllers/ApiApplication.php:82`, attached as a Symfony subscriber) writes a row to `api_log` for every REST/FHIR call (status code, duration, controller, scopes). This is the table to query if you need a record of every API call an agent has made.

## Frontend Composition

The post-login app is `interface/main/tabs/main.php` (572 lines). Its body (`tabs/main.php:455`) is a Knockout MVVM scaffold: a top `navbar` with `data-bind="template: {name: 'menu-template', data: application_data}"` (file:498), an `attendantData` row (file:517) which is the active patient banner, then `<div class="mainFrames d-flex flex-row" id="mainFrames_div"><div id="framesDisplay" data-bind="template: {name: 'tabs-frames', data: application_data}"></div></div>` (file:519-520) — the actual tab strip, where each tab is an `<iframe>` loading a legacy PHP page. Knockout view models in `interface/main/tabs/js/tabs_view_model.js` drive tab open/close and the patient context. Bootstrap 4.6 is the layout system; jQuery 3.7 is the AJAX/DOM library; Smarty 4.5 and Twig 3.x render server-side templates.

There is no built-in right-side panel — the layout is `flex-row` with the menu on top and one full-width frame below. Twig templates live under `templates/` (e.g. `templates/oemr_ui/page_heading/partials/page_heading.html.twig`) and inside form modules at `interface/forms/{form}/templates/*.twig`. The patient summary screen is `interface/patient_file/summary/demographics.php` and its subordinate cards (`clinical_reminders_fragment.php`, `dashboard_header.php`, etc.). Each card is a separate include; the canonical extension hook is the `Patient\Summary\Card\RenderEvent` event noted earlier — listeners can prepend/append `RenderInterface` objects to any card.

Module UI typically opens its own tab via `tabs_view_model.js` (the menu-template route sends new tabs into iframes), or replaces a card via the `Card\RenderEvent`, or injects markup at `Tabs\RenderEvent::EVENT_BODY_RENDER_PRE`/`POST`.

## Deployment Topology

Production reference is `docker/production/docker-compose.yml`: two containers — `mysql` (MariaDB 11.8.6) and `openemr` (image `openemr/openemr:latest`, which is **Apache 2.4 + mod_php** based on Alpine, exposing :80/:443). The single-process model: Apache directly executes PHP via mod_php; there is no FPM in the standard image, no Nginx, no separate worker pool. A health endpoint at `https://localhost/meta/health/readyz` is used by the Docker healthcheck (file:54). Environment is configured via `OPENEMR_SETTING_*` env vars that get inserted into the `globals` table on startup.

`docker/development-easy/` adds Selenium grid (port 4444), CouchDB (6984, used for binary document storage), OpenLDAP (auth backend for local testing), and Mailpit (SMTP capture). Image is `openemr/openemr:flex` — same Apache+mod_php base but with developer tooling (xdebug, devtools script). The MariaDB connection uses TLS with bundled certs at `library/sql-ssl-certs-keys/easy/`.

`docker/development-easy-redis/` adds Redis for session storage. `ci/` has variants: `apache_82_118` through `apache_85_122` (PHP 8.2 to 8.5 against MariaDB 11.8/10.11), `nginx`/`nginx_82-86` (Nginx + PHP-FPM variants, used in CI to verify FPM works), `apache_85_118_redis_sentinel*` (Redis Sentinel HA), and `inferno/` (the FHIR conformance test rig). So the supported deploy shapes are: Apache+mod_php (default), Apache+PHP-FPM, Nginx+PHP-FPM. MariaDB 10.11 LTS to 11.8 are all tested; MySQL 5.7+ and 8 are supported but not the default. Redis is optional for sessions/cache.

## Integration Points for an AI Agent

Ranked by fit for a Clinical Co-Pilot. Each entry: where it lives, how the agent reaches it, one tradeoff.

**1. Custom module + event listeners (`interface/modules/custom_modules/oe-module-clinical-copilot/`).**
Where: a new directory next to `oe-module-weno`, registered in the `modules` table. `openemr.bootstrap.php` instantiates a `Bootstrap` that subscribes to `PatientCreatedEvent`, `PatientUpdatedEvent`, `Patient\Summary\Card\RenderEvent` (`src/Events/Patient/Summary/Card/RenderEvent.php:24`), `Main\Tabs\RenderEvent::EVENT_BODY_RENDER_PRE` (`src/Events/Main/Tabs/RenderEvent.php:20`), `RestApiCreateEvent` (`src/Events/RestApiExtend/RestApiCreateEvent.php:10`).
How the agent accesses: in-process — listeners run inside the OpenEMR PHP request, can call `PatientService`/`EncounterService` directly, can write back to the DOM via `Card\RenderEvent::addAppendedData`, can inject `<script>` tags via `Tabs\RenderEvent`, can register new REST routes via `RestApiCreateEvent::addToRouteMap`.
Tradeoff: tightly coupled to OpenEMR's PHP runtime and version (PHP 8.2+, Symfony component versions). Module survives upgrades only if the event signatures stay stable; deploys as part of OpenEMR, not separately.

**2. SMART on FHIR app (external).**
Where: register a SMART client at `/oauth2/{site}/registration` or via the existing OAuth2 admin UI, host the agent on its own infrastructure, point it at the EHR's FHIR base. Discovery at `/fhir/.well-known/smart-configuration` (`src/RestControllers/SMART/SMARTConfigurationController.php:45`). Patient launch via the SMART standalone or EHR-launch flow.
How the agent accesses: standard FHIR R4 over HTTPS with OAuth2 bearer token. All FHIR endpoints in `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php`. PKCE supported (`code_challenge_methods_supported: ['S256']`).
Tradeoff: zero coupling to OpenEMR internals; runs anywhere; standardized auth. But limited to what FHIR US Core 3.1.0/7.0.0 exposes — no access to encounter-form free text, no proprietary tables, write coverage is patchy. UI integration is via "launch from menu" iframe only; no card injection.

**3. Custom REST controller behind LocalApi (in-process, machine-to-machine).**
Where: add a new file `src/RestControllers/CopilotRestController.php` and register routes in a module via `RestApiCreateEvent::addToRouteMap('POST /api/copilot/chat', ...)` (file:50). The module bootstrap can subscribe to that event.
How the agent accesses: HTTPS POST to `/apis/{site}/api/copilot/chat` with a Bearer token (any clinician's OAuth2 token works) or via the in-process `LocalApiAuthorizationController` if called from another OpenEMR component. ACL check via `RestConfig::request_authorization_check`.
Tradeoff: full access to all internal services and all 281 tables, but the agent is now part of the OpenEMR security boundary — every code change goes through OpenEMR's release/audit. Can call `EncounterService::getEncountersForPatient` directly without going through FHIR, useful if the agent needs the raw `form_*` rows.

**4. Frontend chat panel injected via `Main\Tabs\RenderEvent::EVENT_BODY_RENDER_POST`.**
Where: a module listener that, on every render of `interface/main/tabs/main.php:460`, appends a fixed-position `<div id="copilot-panel">` plus a `<script>` that mounts a chat UI (React/Vue/vanilla). The script reads `top.csrf_token_js`, `top.api_csrf_token_js`, `top.webroot_url`, and `top.site_id_js` (all defined at `tabs/main.php:132-141`) to authenticate AJAX calls.
How the agent accesses: the panel runs in the user's browser context, calls back to a custom REST endpoint (option #3) or directly to `library/ajax/*` endpoints (with `csrf_token_form`). Patient context is `top.getSessionValue('pid')` (`tabs/main.php:173-191`).
Tradeoff: clean UI placement, persistent across tabs, has full session — but lives at the parent-frame level and any iframe navigation does not unload it. Requires careful CSP work; OpenEMR sets `X-Frame-Options: DENY` on the login page and tightens CSP elsewhere.

**5. Patient summary card via `Card\RenderEvent::addAppendedData`.**
Where: a module listener for `'patientSummaryCard.render'` that calls `addAppendedData(new RenderInterface)` on the demographics or clinical-reminders card (`src/Events/Patient/Summary/Card/RenderEvent.php:85-89`).
How the agent accesses: server-side render hooks inject HTML/Twig into specific cards on `interface/patient_file/summary/demographics.php`; the resulting markup can carry an inline chat or a "Ask Copilot" button that opens a dialog and calls back to a custom endpoint.
Tradeoff: precisely the right place to surface patient-specific suggestions next to the patient banner. But limited to that single screen — does not appear on encounter view, calendar, billing, etc. Combine with #4 for full coverage.

**6. CDS Hooks endpoint (FHIR-adjacent standard).**
Where: not currently implemented in OpenEMR, but the SMART scope infrastructure (`src/Common/Auth/OpenIDConnect/Repositories/ScopeRepository.php`) and `DecisionSupportInterventionService.php` (`src/Services/DecisionSupportInterventionService.php`) plus the `dsi_source_attributes` table suggest the framework is partially built. CDS Hooks would need new routes registered via `RestApiCreateEvent`.
How the agent accesses: standard CDS Hooks JSON over HTTPS — OpenEMR fires hooks like `patient-view`, `order-sign`, agent returns cards.
Tradeoff: standardized integration that avoids custom UI work, but requires building the hook firing logic at OpenEMR's hot paths (encounter open, prescription sign). Larger lift than #1.

**7. Direct Symfony EventDispatcher subscriber for write-time auditing/intervention.**
Where: in-process listener for `BeforePatientUpdatedEvent` / `BeforePatientCreatedEvent` (`src/Services/PatientService.php:188-190, 268-269`). These events are *mutable* — the listener can rewrite the data array.
How the agent accesses: synchronous PHP listener that inspects/mutates the data hash and optionally calls out to an LLM (with timeout safeguards).
Tradeoff: lets the agent veto or transform writes — strong intervention capability — but blocks the request thread. An LLM round-trip on every patient save is a non-starter without async-with-confirmation UX.

**8. Background service via the BackgroundService REST endpoint.**
Where: `BackgroundServiceRestController.php` plus the `background_services` table; `interface/main/tabs/main.php:270` shows the `/apis/{site}/api/background_service/$run` POST that runs every 60 seconds. Agent registers as a service and gets polled.
How the agent accesses: register a row in `background_services`, the polling loop will call the registered handler. The handler can run inference, write reminders to `patient_reminders`, etc.
Tradeoff: built-in scheduling and retry, no UI changes — but eventual consistency only, no synchronous user feedback. Good for batch suggestions, bad for chat.

**9. Patient portal extension point.**
Where: separate `portal/` codebase with its own session and auth (`apis/routes/_rest_routes_portal.inc.php` + `interface/portal/`). The agent could expose a patient-facing chat in the portal via similar techniques as #4.
How the agent accesses: portal session, portal-specific OAuth2 flow.
Tradeoff: completely separate from clinician UX; useful if the product needs both, but doubles the integration work.

**10. SQL audit via `EventAuditLogger` for action attribution.**
Where: any code that wants its actions logged with HIPAA-shaped metadata calls `EventAuditLogger::getInstance()->newEvent($eventType, $user, $provider, $success, $comment)` (`src/Common/Logging/EventAuditLogger.php`). The audit logger has its own DB connection so writes survive transaction rollback.
How the agent accesses: every agent-initiated write should attribute to a synthetic `agent` user (or the clinician on whose behalf it runs) and emit a custom event type. Combine with `extended_log` for non-PHI agent telemetry.
Tradeoff: not an integration *point* per se but a required surface — anything an agent does to PHI must end up in the audit log to satisfy HIPAA. Cost is low (one method call per action), but audit-log visibility for end-users (`interface/logview/`) is not designed for high-volume agent activity and may need a separate UI.

## Out of scope / not investigated

- The CCDA service (`ccdaservice/` is a Node.js sidecar that handles C-CDA generation and CQM evaluation) — runs as a separate process, registered via `OPENEMR_SETTING_ccda_alt_service_enable` in compose; would be its own audit if relevant.
- The portal codebase (`portal/`, `interface/portal/`) — confirmed it exists and has its own routes, but full surface area not mapped.
- The Zend MVC modules under `interface/modules/zend_modules/module/` (Carecoordination, Patientvalidation, Immunization, etc.) — confirmed they bootstrap via Laminas in `ModulesApplication`, but per-module APIs not catalogued.
- Internal Service Container (`OpenEMR\BC\ServiceContainer`) and Symfony DI compiler passes in `Kernel::prepareContainer` — touched on but not exhaustively mapped; relevant for understanding how to inject services into an agent module.
- The HL7 v2 / X12 / DICOM ingestion paths (`interface/orders/`, `library/edihistory/`, `interface/dicom_frame.php`).
- The patient-portal SMART app (`portal/smart/`) — distinct from the clinician-facing SMART entry points.
- Detailed mapping of phpGACL ACL section/value pairs (`gacl_*` tables) — there are ~100 ACL pairs; the audit only covers the pattern of how they're checked (`AclMain::aclCheckCore`), not the full enumeration.
- The `meta/` health endpoint internals — only confirmed that `/meta/health/readyz` is the production healthcheck.
- Performance characteristics (DB indexes, slow queries) and HA topology — out of scope for this architecture audit.
