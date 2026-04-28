# Compliance Audit — Raw Findings

Audit target: OpenEMR codebase at `/Users/macbook/dev/Gauntlet/week1/openemragent` (commit `63016b1`).
Scope: HIPAA Security Rule §164.312 / Privacy Rule §164.524–528 + Breach §164.408 + BAA §164.504(e).
Focus: pre-deployment audit for a Clinical Co-Pilot agent that will send PHI to LLM providers under signed BAAs.

## Summary — what would actually block real-PHI deployment

- **Audit tamper-evidence is one-way.** Every log row gets a SHA3-512 row-hash written to `log_comment_encrypt.checksum`, but nothing in the codebase reads the chain back to verify it. There is no daily verifier, no ATNA replay, no startup check. Tamper detection is theoretical. (`src/Common/Logging/Audit/LogTablesSink.php:61,88`)
- **The audit log is purgeable from the admin UI.** `interface/main/backup.php:1045-1056` (form_step 405) lets any super-admin run `DELETE log, lce, al ... WHERE log.date <= ?`. There is no append-only enforcement, no off-site copy required, no minimum-retention guardrail. HIPAA wants 6 years (§164.316(b)(2)(i)); the code has zero retention enforcement.
- **Core OpenEMR session cookie is NOT secure-only and NOT httpOnly.** `src/Common/Session/SessionConfigurationBuilder.php:26-27` defaults `cookie_secure=false, cookie_httponly=false`, and `forCore()` (line 88) explicitly sets `cookie_httponly=false` "since javascript needs to be able to access/modify the cookie." Session ID is exposed to any XSS and any plaintext transit. Patient portal & OAuth sessions ARE secure; only the clinician-facing core session is not.
- **HTTPS is not enforced anywhere in the application.** No redirect-to-HTTPS PHP middleware, no HSTS header set, `.htaccess.example` has zero TLS rules. TLS termination is delegated entirely to the deploying operator. There is no PHP-level guard that refuses to serve PHI over HTTP.
- **Patient delete is a hard SQL DELETE.** `interface/patient_file/deleter.php:252` runs `DELETE FROM patient_data WHERE pid = ?` plus 14 sibling DELETEs. After this runs, the patient is forensically gone, which conflicts with the 6-year retention obligation for accounting-of-disclosures records on the same patient. Only `billing` and `pnotes` are soft-deleted.
- **Patient-record reads via the legacy UI are audited only by SQL-statement matching, not at the read API.** `EventAuditLogger::auditSQLEvent` (`src/Common/Logging/EventAuditLogger.php:390-510`) intercepts every ADODB query and decides whether it touched a `LOG_TABLES` table. SELECTs through Doctrine DBAL or direct PDO bypass this. `src/Common/Database/QueryUtils.php` uses both paths, so there is no single audit chokepoint.
- **No MFA enforcement.** TOTP/U2F machinery exists (`src/Common/Auth/MfaUtils.php`) but enrolment is per-user opt-in via `login_mfa_registrations`. There is no global setting like "require MFA for all users" or "require MFA for users in role X." A clinician-with-PHI-access can have a password-only account in the shipped configuration.
- **The agent layer (LLM, embeddings, vector DB, observability) introduces 4–6 net-new BAA-required vendors and a brand new audit table that does not exist today** (per-tool-call: prompt, response, tokens, latency, model, decision). The current `log` table cannot represent this — see "What the AI Agent Layer Adds" below.

## Audit Logging (§164.312(b))

- **[CRITICAL] Audit hash chain is generated but never verified** — `src/Common/Logging/Audit/LogTablesSink.php:61,81,88`. Each log row gets `hash('sha3-512', implode('', array_values($logData)))` stored in `log_comment_encrypt.checksum`. No code anywhere recomputes and compares — `grep -rn "verify.*checksum"` over the audit subsystem returns zero hits. The "tamper-evident" property requires an external verifier that does not exist. §164.312(b) audit controls require the ability to detect tampering; storing a hash without ever checking it is theatre.
- **[CRITICAL] Audit log is admin-purgeable with no minimum retention** — `interface/main/backup.php:1045-1056`. UI flow `form_step=405` calls `DELETE log, lce, al FROM log LEFT JOIN log_comment_encrypt LEFT JOIN api_log WHERE log.date <= ?`. Default end_date is "2 years ago, end of year" (line 1032), well below the 6-year HIPAA retention requirement (§164.316(b)(2)(i)). There is no "sealed audit storage" path.
- **[HIGH] Audit logging is master-toggleable** — `src/Common/Logging/AuditConfig.php:23` reads `enable_auditlog` from `OEGlobalsBag`. If the global is false, ALL non-breakglass audit writes are silently skipped at `EventAuditLogger.php:395-399`. A super-admin can disable auditing without leaving any indication except whatever `auditSQLAuditTamper` writes (line 518 — but that depends on the toggle being audited via the same disabled subsystem to begin with). A misconfigured tenant can run for months with no audit trail.
- **[HIGH] SELECT auditing is opt-in** — `EventAuditLogger.php:425-429`. SELECT statements are dropped unless `audit_events_query` is true. The default (per `library/globals.inc.php` patterns) is false. So in stock OpenEMR, reads of patient records are NOT logged unless an admin explicitly opted in. HIPAA §164.312(b) wants every PHI access logged.
- **[HIGH] Audit hook lives at the ADODB driver layer, not at the service layer** — `library/ADODB_mysqli_log.php:50` calls `EventAuditLogger::getInstance()->auditSQLEvent($sql, $outcome, $inputarr)` after every Execute. New code that uses Doctrine DBAL directly (`src/BC/DatabaseConnectionFactory.php::createDbal`) bypasses this entirely. Several services use DBAL — every one of those is an audit blind spot.
- **[HIGH] Audit category is inferred from SQL substring matching** — `EventAuditLogger.php:471-481`, `eventCategoryFinder` (line 744). The classifier does `str_contains($truncated_sql, $table)` against a hardcoded `LOG_TABLES` map (line 116). New tables added in modules (e.g. `oe-module-weno`, `oe-module-faxsms`) are not in the map and are categorised as "other" — and "other" SELECTs are dropped at line 487. PHI access via custom modules effectively bypasses audit.
- **[MEDIUM] ATNA sink emits per-row, not the canonical RFC 3881 record** — `src/Common/Logging/Audit/AtnaSink.php:32-50` builds a fixed XML template with one `ActiveParticipant` source / one destination / one user. Lab order audits, e-prescribe transmissions, and FHIR API reads all share the same shape. The displayName mapping (line 146) reduces granularity to ~6 buckets. ONC presentations sometimes accept this, but a strict IHE ATNA validator will reject it.
- **[MEDIUM] `extended_log` (disclosures) has no checksum/encryption parity** — `sql/database.sql:12414-12423`. `extended_log` is plain rows; unlike `log` it has no `log_comment_encrypt` companion. So per-disclosure HIPAA §164.528 records are not tamper-evident at all.
- **[MEDIUM] `api_log` stores full request body and response** — `src/RestControllers/Subscriber/ApiResponseLoggerListener.php:77-86` and `src/Common/Logging/Audit/LogTablesSink.php:69-79`. If `api_log_option == 2` and audit encryption is OFF, the FHIR JSON body (containing PHI) is base64'd into `api_log.request_body` / `api_log.response`. With `enable_auditlog_encryption` true it is encrypted with `CryptoGen`; with false, it is plaintext at rest. The default state is determined per-deployment.
- **[MEDIUM] Patient view IS audited at session level, but not at every row read** — `src/Common/Session/PatientSessionUtil.php:58` writes a single `view` event when `setPid()` is called. Within a session, a user can pull dozens of forms / labs / notes against the same pid with no per-record entry; only the auditSQLEvent SELECT-table-name matching produces extra entries (and only if `audit_events_query` is on).
- **[LOW] Disclosure recording is manual** — `src/Common/Logging/EventAuditLogger.php:552-567` `recordDisclosure` writes to `extended_log`. There's no automated wiring; users must manually open `interface/patient_file/summary/record_disclosure.php` and submit. CCDA exports, FHIR pushes, lab orders, fax-outs do NOT auto-create disclosure records.
- **[LOW] ATNA outbound is best-effort UDP/TCP** — `src/Common/Logging/Audit/Atna/TcpWriter.php`. If the SIEM is down, audit events to ATNA are lost; only the local DB sink is durable. There's no queue / retry.

## Authentication and Access Control (§164.312(a))

- **[CRITICAL] No global MFA-required setting** — `src/Common/Auth/MfaUtils.php:74` `isMfaRequired()` is true ONLY if the user has rows in `login_mfa_registrations`. There is no `gbl_require_mfa_all_users` or `gbl_require_mfa_clinicians`. An admin can create a new user with no MFA enrolled, and that user can log in with password alone.
- **[CRITICAL] Core session cookie not secure / not httpOnly** — `src/Common/Session/SessionConfigurationBuilder.php:26-27` defaults are `cookie_secure=false, cookie_httponly=true` for the builder, but `forCore()` (line 88) explicitly OVERRIDES `setCookieHttpOnly(false)` and never calls `setCookieSecure(true)`. Documented in `src/Common/Session/SessionUtil.php:8-14` as required by the legacy `restore_session()` JavaScript. Patient-portal (line 117) and OAuth (line 96) sessions ARE secure; only clinician-facing OpenEMR is not. An XSS in the EMR steals the session ID directly from `document.cookie`.
- **[HIGH] No HTTPS enforcement in the app** — searched `src/`, `library/`, `interface/main/`, `index.php`, `.htaccess.example`. No `header('Strict-Transport-Security')`, no `header('Location: https://...')`, no `if ($_SERVER['HTTPS'] != 'on')` guard. TLS depends entirely on the operator's webserver config. §164.312(e)(1) requires transmission security; the app trusts the network.
- **[HIGH] Default idle session timeout is 2 hours** — `library/globals.inc.php:2105-2110` `timeout` default `'7200'`. NIST 800-53 / HIPAA recommended for clinical workstations is typically 15-30 minutes. `portal_timeout` (line 2111) defaults to 30 min — that's the patient portal, which is correct, but the clinician side is too long.
- **[HIGH] LDAP fallback has no MFA hook** — `library/globals.inc.php:2285-2308` `gbl_ldap_enabled`. When LDAP authentication is enabled, the MFA flow at `MfaUtils` is bypassed depending on configuration. This needs review per-tenant before any LDAP-on / MFA-required deployment.
- **[MEDIUM] Password complexity is weakly enforced** — `library/globals.inc.php:2123` `secure_password` default `'1'` (require strong); `gbl_minimum_password_length` default `9` (line 2152). `password_history` default `5` (line 2176), `password_expiration_days` default `180` (line 2183). All settings are admin-toggleable. There is no UI lock that prevents an admin from setting `gbl_minimum_password_length=4` or disabling `secure_password` without leaving an audit trail beyond a generic globals-update log entry.
- **[MEDIUM] Failed-login lockout is per-user counter only, not per-account-lockout-policy** — `library/globals.inc.php:2194-2206`. Default `password_max_failed_logins=20`, reset window `3600`. After 20 failures the account is locked, but the reset is automatic. There is no manual unlock requirement, no admin notification.
- **[MEDIUM] Breakglass user identity is matched by ARO group `breakglass`** — `src/Common/Logging/BreakglassChecker.php:49-58` and `Documentation/Emergency_User_README.txt`. A breakglass user is identified by membership in the `breakglass` ACL group. The convention is "username starts with breakglass or emergency" (per docs line 20-21) but the code does NOT enforce that — it uses ARO group membership only. Also note the docs say "Disable or delete the emergency account(s) that were used to prevent re–use" — this is a manual operator step with no automation.
- **[MEDIUM] Breakglass forces auditing only when `gbl_force_log_breakglass` is true** — `EventAuditLogger.php:396,426,501`. If the global is false, breakglass users get the same audit-toggle treatment as regular users, which means a tenant with `enable_auditlog=false` and `gbl_force_log_breakglass=false` has zero break-glass-specific traceability.
- **[LOW] Auth hash algorithm is admin-selectable down to weak options** — `library/globals.inc.php:2317-2328` `gbl_auth_hash_algo`. Options include `SHA512HASH` ("ONC 2015"). PHP-default (bcrypt) is fine; but the menu still lets you choose SHA512.

## Encryption (§164.312(a)(2)(iv), (e)(1))

- **[CRITICAL] KEK lives on the local filesystem in plaintext-after-base64** — `src/Common/Crypto/CryptoGen.php:421` `$keyPath = $this->siteDir . "/documents/logs_and_misc/methods/" . $label`. The drive key is base64'd (legacy versions) or AES-encrypted-by-the-DB-key (current). The DB key is stored in `keys` table also base64'd (`src/Encryption/Storage/PlaintextKeyInDbKeysTableQueryUtils.php:45`). Anyone with read on both the DB and `sites/<site>/documents/logs_and_misc/methods/` decrypts everything. There is no HSM/KMS integration. Per §164.312(a)(2)(iv) "Encryption" is an addressable spec; storing keys in cleartext-decoded form next to the data they protect is a documented anti-pattern.
- **[CRITICAL] Keys directory must be web-readable to the PHP process and is on the same volume as documents** — `src/Common/Crypto/CryptoGen.php:9` documents the path. A path-traversal or LFI in any PHP page that accidentally reads from `sites/default/documents/...` would expose the KEK. The only protection is `sites/default/documents/.htaccess` which contains "Deny From All" (verified) — that's apache-only, fails open on non-Apache servers (nginx, frankenphp), and does not protect against in-process reads.
- **[HIGH] Two parallel crypto systems: `Common/Crypto/CryptoGen` (legacy) and `Encryption/CipherSuite` (new)** — both registered through `OpenEMR\BC\ServiceContainer::getCrypto()`. `EventAuditLogger.php:810-817` does `if instanceof CipherSuiteInterface ... else CryptoInterface->encryptStandard($plaintext)`. The boundary between the two is not documented; new code can pick either. For the agent we need to standardize.
- **[HIGH] At-rest encryption of patient data is opt-in per-field, not column-level** — `CryptoGen::encryptStandard()` is called at the application layer. There is no transparent column encryption. `patient_data.ss` (SSN), `patient_data.drivers_license`, etc. (`sql/database.sql` patient_data table) are stored as `varchar(255)` plaintext. Operator must enable MySQL TDE / encrypted volumes for at-rest encryption of demographics. The codebase does not assume that.
- **[HIGH] Backups dump plaintext SQL** — `interface/main/backup.php:1015-1020` calls `mysqldump ... -r emr_backup.tar.gz`. The output is gzipped SQL, not encrypted. `sites/default/documents/` is included in `openemr.tar.gz` (line 8) — meaning the backup tarball contains the DB dump AND the on-disk KEK. A stolen backup is a full data leak.
- **[HIGH] In-transit DB encryption is opt-in** — `src/BC/DatabaseConnectionFactory.php:40-45` only sets `MYSQLI_CLIENT_SSL` if `$config->sslCaPath !== null`. The default in `DatabaseConnectionOptions` does not enforce SSL. Any app→MySQL traffic is plaintext on the network unless the operator opts in.
- **[MEDIUM] Audit-log encryption (`enable_auditlog_encryption`) is optional** — `EventAuditLogger.php:89,645-658`. When false, comments are base64'd plaintext. Audit comments include SQL statements with bound values — i.e. real PHI data — at line 431-437. Default is per-deployment.
- **[MEDIUM] HMAC over CBC uses sha384, encryption is AES-256-CBC, IV is per-message random** — `CryptoGen.php:159-180`. Crypto primitives are correct (AES-256-CBC + Encrypt-then-MAC with HMAC-SHA384); but CBC-mode is older. AES-GCM via the new `Encryption\CipherSuite` is the better forward path. Don't mix.
- **[LOW] Backup tarball naming is predictable** — `backup.php` writes `emr_backup.tar` to a known temp dir. If web-served via a misconfigured route, an attacker can fetch.

## Retention and Disposal (§164.524, §164.528)

- **[CRITICAL] Patient delete is a hard cascade DELETE** — `interface/patient_file/deleter.php:252` and surrounding lines 220-244 delete from `patient_data, prescriptions, claims, payments, immunizations, issue_encounter, lists, transactions, employer_data, history_data, insurance_data, patient_history, forms, form_encounter`. Soft-delete only on `billing` (`activity=0`), `pnotes` (`deleted=1`), `ar_activity` (`deleted=NOW()`). After delete, you cannot produce an accounting-of-disclosures (§164.528) for that patient — a regulatory contradiction since the disclosure obligation is 6 years, not "until the patient is deleted."
- **[CRITICAL] No documented retention policy** — searched `Documentation/`, `README.md`, `CONTRIBUTING.md`, `*.md` at root. No retention rules, no purge schedule, no archival path, no "audit log retained for 6 years" doc. `Documentation/README-Log-Backup.txt` describes the backup mechanic but says nothing about retention.
- **[HIGH] Audit log purge UI has no minimum-age guardrail** — `interface/main/backup.php:1031-1042`. The default end_date is "2 years ago" but there is no MIN(date) enforcement. An admin can put `end_date = today` and wipe the entire log. UI offers "Download Log Entries as Zipped CSV" before delete (line 1041) but that's voluntary.
- **[HIGH] No audit log offsite/append-only mode** — there is no S3-bucket sink, no WORM mode, no immutable-table option. ATNA writer (`Atna/TcpWriter.php`) is fire-and-forget UDP/TCP. The only durable copy is local MySQL.
- **[MEDIUM] Documents are soft-deleted but the file may still exist on disk** — `delete_document` in `deleter.php` (called line 249, 298) marks `documents.deleted=1` but the underlying file in `sites/default/documents/...` is not necessarily removed by the same code path. (Verify per-installation; this scope did not chase the document service deletion.)
- **[MEDIUM] `extended_log` (disclosures) has no retention enforcement** — `sql/database.sql:12414`. `recordDisclosure` adds rows; `deleteDisclosure` (`EventAuditLogger.php:607-611`) deletes by `id`. No cron purge, no audit-of-the-audit; an admin can simply delete a disclosure record.

## Patient Rights

- **[HIGH] Right of access via portal exists but is opt-in per-patient** — `src/Services/PatientAccessOnsiteService.php:118-127`. The portal credential is created by staff and emailed to the patient (line 261 area). There is no "patient self-registration" path that produces an automatic access link. Right-of-access requests under §164.524 require fulfilment in 30 days; the workflow is manual.
- **[HIGH] Right of amendment is implemented but lacks a portal-side request flow** — `interface/patient_file/summary/add_edit_amendments.php:90-104` writes to `amendments` and `amendments_history`. `portal/get_amendments.php` lets the patient READ amendments but I did not find a portal-write path that creates an amendment-request. Patients call/email and staff manually enter, which is a §164.526 workflow but not auditable end-to-end.
- **[HIGH] No "accounting of disclosures" report generator** — Disclosures are stored in `extended_log` (`EventAuditLogger.php:552`). `interface/patient_file/summary/disclosure_full.php:111-118` queries them. There is no patient-facing or staff-export "produce 6 years of disclosures for patient X" report. It must be assembled manually from the SQL. §164.528 requires this on request within 60 days.
- **[MEDIUM] `amendments_history` has no `modified_time` field on the history rows** — `sql/database.sql:76-83`. The history table tracks who created it (`created_by`) and when, but cannot represent updates to a history row, by design (history is append-only). This is correct, but means amendments-history SHOULD be append-only at the DB layer (no `DELETE FROM amendments_history`); the schema does not enforce that.

## Breach Notification (§164.408)

- **[HIGH] No incident-detection or notification scaffolding** — searched for `breach`, `incident`, `notify`, `siem`, `cef`, `leef`. No webhook, no email-on-failure for repeated failed logins, no SIEM bridge beyond the ATNA TCP sink. `library/globals.inc.php:2257-2262` `Emergency_Login_email_id` sends an email when a breakglass user activates — that's the closest thing.
- **[HIGH] No syslog forwarding for security events** — `src/Common/Logging/SystemLogger.php:62` writes via Monolog `ErrorLogHandler::OPERATING_SYSTEM`, which goes to PHP's `error_log` destination (typically a flat file or local syslog). There is no remote syslog handler, no Logstash, no Splunk HEC. The ATNA sink (`AtnaSink`) is the only remote audit egress, and it carries audit events not application errors.
- **[MEDIUM] Failed-login alerting is not built in** — `AuthUtils.php` increments counters in `users_secure` but does not email anyone on N consecutive failures, even per-IP. `password_max_failed_logins` (default 20) just locks the user out.

## BAA Inventory — Existing Third-Party PHI Pathways

These are integration points already in the codebase that send PHI to external systems. Each needs an existing or new BAA before clinical go-live.

- **[HIGH] Weno e-prescribing (cloud)** — `interface/modules/custom_modules/oe-module-weno/`. Sends prescriptions to `online.wenoexchange.com` (see `scripts/file_download.php:25`, `templates/rxlogmanager.php:38`). Path includes patient demographics, drug, and prescriber DEA number (`Pharmacy.class.php:256`). EPCS endpoint is mentioned (`/EPCS/...` URLs). **Weno BAA REQUIRED.**
- **[HIGH] Lab orders (LabCorp, Quest, generic HL7)** — `interface/procedure_tools/labcorp/gen_hl7_order.inc.php:752`, `interface/procedure_tools/quest/gen_hl7_order.inc.php:483`, `interface/procedure_tools/gen_universal_hl7/gen_hl7_order.inc.php:468`. HL7 orders include name, DOB, MRN, dx codes, ordering provider. **LabCorp BAA, Quest BAA, plus any custom HL7 endpoint BAA REQUIRED.**
- **[HIGH] CCDA / FHIR push outbound** — `ccdaservice/serveccda.js`, `src/Services/Cda/`, FHIR API at `apis/`. CCDA contains demographics + clinical data; pushed to whatever CCDA recipient an admin configures. **Per-recipient BAA.**
- **[HIGH] Direct messaging (DIRECT protocol)** — `library/direct_message_check.inc.php:36,678` uses PHPMailer + DIRECT. PHI flows out by design. **Per HISP BAA.**
- **[HIGH] Fax/SMS module** — `interface/modules/custom_modules/oe-module-faxsms/`. Module supports RingCentral and other providers (multiple `setup_*.php` files). Faxes contain clinical content. **RingCentral / Twilio / vendor-specific BAA.**
- **[HIGH] Patient portal email (notifications, password reset, login link)** — `src/Services/PatientAccessOnsiteService.php:261` calls email service with portal username/password. Email body contains identifiable info. **SMTP relay / Mailgun / SendGrid / Postmark BAA REQUIRED.**
- **[MEDIUM] Google Sign-In** — `library/globals.inc.php:2271-2283`. If `google_signin_enabled` is on, user identity flows through Google. Google does NOT sign a BAA for consumer Sign-In; you'd need GCP Workspace BAA scope. Recommend disabling.
- **[MEDIUM] LDAP server** — `library/globals.inc.php:2285-2308`. If outsourced (e.g. Azure AD), the auth provider sees usernames + login times for clinicians. **Azure AD / Okta BAA depending on tenant.**
- **[MEDIUM] Telehealth module (Comlink)** — `interface/modules/custom_modules/oe-module-comlink-telehealth/`. Video sessions = PHI.
- **[MEDIUM] DORN, ClaimRev** — other third-party modules in `interface/modules/custom_modules/`. Need per-vendor review.

## BAA Inventory — Future Agent PHI Pathways

What the Clinical Co-Pilot adds. **Each requires a signed BAA before real-PHI traffic.**

- **[CRITICAL] LLM provider** — Anthropic (Claude), OpenAI, Azure OpenAI, AWS Bedrock, GCP Vertex. Whichever provider receives the prompt sees patient name, dx, meds, labs, problem list, plan-of-care notes. **REQUIRED BAA.** Anthropic and OpenAI both offer BAAs on enterprise tiers; Bedrock/Vertex inherit cloud-provider BAA. Document which model versions are BAA-eligible (e.g., not all OpenAI models are covered under their BAA).
- **[CRITICAL] Embedding provider** — same vendors as above for vector embeddings of clinical notes. **REQUIRED BAA** — and note that even "anonymized" embeddings can be inverted, so treat them as PHI.
- **[CRITICAL] Vector database / retrieval store** — Pinecone, Weaviate, Qdrant Cloud, Postgres+pgvector, Chroma. If hosted, **REQUIRED BAA**. Self-hosted Postgres+pgvector inside the same VPC as MySQL is the cleanest path.
- **[CRITICAL] Observability platform** — Langfuse, LangSmith, Helicone, Datadog LLM Observability. If they receive prompts/responses for tracing, **REQUIRED BAA**. Langfuse self-hosted is BAA-free (no third party). LangSmith Cloud requires LangChain Enterprise + BAA; verify before use.
- **[HIGH] Error tracking** — Sentry, Rollbar, Bugsnag. Stack traces containing PHI fragments are routine. **REQUIRED BAA** if cloud, OR scrub PHI before send via beforeSend hook.
- **[HIGH] Object storage (audio recordings, images, attachments processed by the agent)** — S3, GCS, Azure Blob. Cloud-provider BAA covers this; need to scope the bucket.
- **[MEDIUM] Application performance monitoring** — Datadog APM, New Relic. If APM captures HTTP body samples that contain PHI. **REQUIRED BAA OR disable body sampling.**
- **[MEDIUM] Background queue / orchestrator** — if the agent uses a hosted queue (SQS, GCP Tasks, Inngest, Trigger.dev). Anything that durably stores message bodies needs BAA OR encrypt-payload-with-tenant-key before enqueue.

## Minimum Necessary / Patient-Level Scoping (§164.502(b))

- **[CRITICAL] `aclCheckCore` does not take a `pid` parameter** — `src/Common/Acl/AclMain.php:166`. Signature is `aclCheckCore($section, $value, $user = '', $return_value = '')`. There is no patient-level scoping at the ACL layer. A user with `patients/med` permission can read EVERY patient's medical record, not just their own panel. The "minimum necessary" requirement (§164.502(b)) is enforced only by UI conventions, not by the data-access layer.
- **[HIGH] Sensitivities ACL exists but is encounter-level, not patient-level** — `src/Common/Acl/AclExtended.php:54-69`. `sensitivities` ACL section gates access to encounters tagged with a sensitivity (e.g. `high`, `STD`, mental health). The encounter table has a `sensitivity` column. But there is no `patient_data.sensitivity` — a patient cannot be globally marked sensitive; you must mark each encounter. New encounters default to whatever the form sets (`EncounterRestController.php:52,110` shows default `'normal'`).
- **[HIGH] Facility/warehouse permissions are off by default** — `library/globals.inc.php:2222-2227` `gbl_fac_warehouse_restrictions` default `'0'`. Even when on, the model is "user can act in facility X," which is a coarser scope than per-panel.
- **[MEDIUM] Squad-based scoping exists for sports-team use only** — `src/Common/Acl/AclExtended.php:44-52`. Note "This is only applicable for sports team use." Not generalized.
- **[MEDIUM] No "patient-record-was-marked-restricted" event audit** — even when `sensitivities` is used, denial events from `aclCheckCore` are not specifically audited as a "minimum necessary breach attempt."

## Logging Hygiene (PHI in non-audit logs)

- **[HIGH] `error_log` calls in business code include `pid`** — `src/Services/Qrda/QrdaReportService.php:136`: `error_log(errorLogEscape(xlt('Patient did not qualify') . ' pid: ' . $pid . ...))`. PID alone is a HIPAA identifier (it's a patient ID, even if internal). PHI fragments in `error_log` go to whatever `php.ini error_log` is configured to — typically a flat file with no encryption, picked up by Datadog/CloudWatch agents at the host level.
- **[HIGH] `PatientSessionUtil.php` writes the requested pid to `error_log` on the failed-int path** — `src/Common/Session/PatientSessionUtil.php:32-34`. Three error_log calls with the raw pid value. `errorLogEscape` is a string sanitizer for log injection, NOT a PHI redactor.
- **[HIGH] `SystemLogger` does NOT redact PHI** — `src/Common/Logging/SystemLogger.php:77-113`. The `escapeVariables` method only does string-escape via `errorLogEscape`. If a service calls `$logger->info('processing', ['patient' => $patient_object])` it gets `json_encode($patient_object)` (line 103) and the patient's name/DOB/SSN/etc lands in the syslog. No redaction layer.
- **[MEDIUM] Exception messages can stringify PHI** — `CLAUDE.md` warns "Never expose `$e->getMessage()` in user-facing output," but no equivalent rule for log output. A SQL exception message includes the SQL statement, including bound values (`EventAuditLogger.php:431-437` shows binds get stringified into the audit comment, which is OK in audit but the same pattern leaks into error_log when audit is disabled).
- **[MEDIUM] PHP debug features** — `library/globals.inc.php:2099-2104` `sql_string_no_show_screen` default `'0'` (off!). With this disabled, SQL queries containing PHI are echoed to the screen during certain operations (`deleter.php:84,105` shows `echo text($query)` directly). For deployment, set this to `'1'`.

## ePrescribing / EPCS

- **[HIGH] Weno is the e-prescribing module; EPCS support is via Weno's cloud** — `interface/modules/custom_modules/oe-module-weno/`. URLs target `online.wenoexchange.com/en/EPCS/...`. EPCS-related endpoints exist (DownloadPharmacyDirectory, RxLog, etc.). The actual "two-factor sign + signing certificate" workflow happens INSIDE Weno's UI — OpenEMR redirects users there. So OpenEMR's DEA 21 CFR §1311 surface is the user-handoff and the audit log of the handoff.
- **[HIGH] No app-side EPCS attestation or signing record** — searched `oe-module-weno` for "controlled substance," "Schedule II," "EPCS," "DEA." Returns DEA placeholder fields in user admin (`interface/usergroup/user_admin.php:480` "Weno User ID") but no DEA-21-CFR-1311 signing-event row, no per-prescription "this was a controlled substance, here's the signed receipt." We cannot replay an EPCS audit for a DEA inspector from inside OpenEMR.
- **[MEDIUM] EPCS user provisioning is admin-set, not identity-proofed in app** — DEA 21 CFR §1311.115 requires identity proofing at the LOA-3 level. OpenEMR delegates that to Weno; the app stores `weno_prov_id` (`usergroup_admin.php:299`) and trusts whatever Weno did. The app-side provisioning UI is just a text field. This is fine IF Weno did the IDP, but operators can mis-provision and there's no signed assertion stored locally.
- **[MEDIUM] Provider DEA number stored unencrypted** — `library/classes/Prescription.class.php:1067` formats `provider->federal_drug_id` for output. The DEA number is in the `users` table as plaintext (`sql/database.sql users` schema area). This is convention-acceptable for paper Rx but would be flagged in any DEA audit if the table is also accessible from the patient portal API.

## What the AI Agent Layer Adds to the Compliance Surface

This section is the bridge to the architecture doc — every item below is a NEW obligation introduced by the agent.

**1. New audit table (does not exist today). Required schema:**

The current `log` table cannot represent agent activity. A `model` column does not exist. A `prompt_hash` does not exist. The agent needs a new `agent_audit` (or similar) with at minimum:

```
agent_audit_id           bigint pk
log_id                   bigint fk -> log.id  (parent OpenEMR audit row)
session_id               varchar(64)          (agent session, distinct from PHP session)
user_id                  bigint               (clinician, fk users)
patient_id               bigint               (pid, fk patient_data; nullable for non-PHI calls)
turn_number              int                  (within session)
tool_name                varchar(64)          (e.g. "lookup_labs", "summarize_encounter")
tool_input_redacted      longtext             (PHI-redacted version for casual review)
tool_output_redacted     longtext             (same)
prompt_token_count       int
completion_token_count   int
model                    varchar(64)          (e.g. "claude-opus-4-5")
provider                 varchar(32)          (e.g. "anthropic")
latency_ms               int
decision                 varchar(32)          (e.g. "answered", "refused", "tool_call", "human_handoff")
escalation_reason        varchar(255)         (if refused / handed off)
created_time             timestamp
checksum                 varchar(128)         (sha3-512 row hash, mirrors log_comment_encrypt pattern)
```

Also a `agent_message` table for the actual prompt/response payloads (encrypted with `CryptoGen::encryptStandard`, stored separately so the audit table can be queried efficiently and `agent_message` can be retention-pruned independently).

Plumbing requirements:
- Every agent response MUST produce one row in `log` (event = `agent`, category = `agent`) AND one row in `agent_audit`.
- The `log` row joins existing chain-hashing in `log_comment_encrypt`; the `agent_audit.checksum` extends the chain to LLM-specific fields.
- Every tool call MUST produce its own row in `agent_audit` (one row per tool call, linked back to the session and to a parent row).
- BAA evidence (vendor name + version of BAA effective at time of call) recorded in `agent_audit.provider` and a separate config table `agent_provider_baa(provider, baa_version, effective_from, effective_to)`.

**2. New BAA inventory — minimum 4 BAAs to add.**

Beyond what already exists (Weno, LabCorp, Quest, fax/SMS, email, Direct, etc., listed above), the agent path requires:

- LLM provider BAA (Anthropic enterprise, OpenAI enterprise, or cloud-provider BAA via Bedrock/Vertex/Azure OpenAI).
- Embedding provider BAA (often the same vendor; verify model is in BAA scope).
- Vector store BAA (or self-host).
- Observability/tracing BAA (or self-host Langfuse).
- Optionally: error tracker BAA (Sentry SaaS) or scrub-before-send.

Each BAA must be linked to a config row that the application reads at startup; agent calls must refuse if no current BAA is registered for the configured provider (a fail-closed startup check is required, not just documentation).

**3. New retention policies needed.**

- `log` table for agent rows: 6 years per HIPAA §164.316(b)(2)(i).
- `agent_message` (raw prompts/responses): per-tenant policy. Recommend 30-90 days "hot" + encrypted cold archive to S3-with-Object-Lock for the remaining 6 years. Don't retain raw prompts forever — they have low audit value vs. high breach blast radius.
- `agent_audit` (metadata): same 6 years.
- BAA records: indefinite (audit evidence).

The current OpenEMR purge UI (`interface/main/backup.php:1045-1056`) MUST be patched to refuse to delete agent audit rows whose `created_time` is within retention window. Today it deletes everything by date.

**4. New access control rules.**

- The agent inherits the calling user's ACL. The agent MUST NOT see records the calling clinician cannot see. This means every tool call has to go through `aclCheckCore` (and ideally a patient-scoped variant — see "Minimum Necessary" CRITICAL above).
- Sensitive-encounter ACL (`AclExtended.php:54`) MUST be respected by agent retrieval. If a patient encounter is tagged sensitivity=high, the agent's RAG layer must NOT ingest that encounter for users without high-sensitivity grant.
- Break-glass: if a breakglass user invokes the agent, every agent call inherits the breakglass marker and the audit row gets `agent_audit.breakglass=true`. `BreakglassChecker` already exists and can be reused.

**5. New input validation / output filtering surface.**

- Agent input: every prompt that goes to the LLM provider must be passed through a PHI minimization pass (strip free-text fields the user didn't explicitly intend to include). This is application-level, no equivalent exists in OpenEMR today.
- Agent output: every response must be passed through a PHI-leak detector before display (the model could hallucinate PHI from another patient if RAG retrieval is wrong). No analog exists in the EHR.
- Both passes log to `agent_audit` (input_redaction_applied, output_filter_applied flags).

**6. New error-handling rules.**

- LLM provider 500 / timeout: agent must log + degrade gracefully + surface "AI temporarily unavailable" to clinician. Must NOT fail the whole encounter view.
- Provider returns refusal / safety-block: agent must log the refusal reason in `agent_audit.decision='refused'` and surface to clinician.
- BAA expiry: if the BAA effective date has passed, agent must hard-fail at startup (fail-closed) and log `agent_audit.decision='blocked_no_baa'`.

**7. New encryption obligations.**

- Prompts contain PHI. They must be encrypted in transit (TLS to provider — verify TLS 1.2+ enforced) and at rest in `agent_message` (use `CryptoGen::encryptStandard()` via the `Database` keysource — or migrate to the new `Encryption\CipherSuite`).
- Vector embeddings: treat as PHI. Encrypt at rest in the vector store.
- Cache: if the agent caches LLM responses, the cache is PHI. No memcache without TLS + auth.

**8. Specific OpenEMR integration points the agent lives at.**

- The agent SHOULD use `EventAuditLogger::getInstance()->recordLogItem(...)` for the parent `log` row (event='agent', category='agent') so existing `log_comment_encrypt` checksum chain extends to agent activity automatically. Do NOT bypass.
- Use `OEGlobalsBag` for agent config (provider URL, model name, max tokens) — do NOT add new `$GLOBALS['agent_*']` reads; CLAUDE.md says no.
- Use `ServiceContainer::getCrypto()` for prompt encryption; do NOT instantiate `CryptoGen` directly.
- Inject `ClockInterface` (PSR-20) so per-call timestamps are deterministic in tests — pattern already used in `EventAuditLogger`.
- Patient-scope check: the agent's tool layer MUST call `aclCheckCore` and additionally check `pid` belongs to the user's authorized panel. Today's ACL has no patient-scoping (see CRITICAL above), so the agent will need to add that itself or compose with a panel-membership check.

## Out of scope / not investigated

- Performance / load: not assessed. Audit-table volume under agent traffic could spike — needs a load test before go-live.
- Backup encryption mechanics in detail: `backup.php` writes an unencrypted tarball; whether the operator's storage is encrypted (volume-level) is out of scope.
- Specific PHPStan rules in `tests/PHPStan/Rules/` that may already enforce some of the above (forbidden-globals, namespace rules) were noted but not enumerated.
- Patient portal authorization (OAuth2 server in `oauth2/`) was looked at only at the session-cookie level. SMART-on-FHIR scoping rules were not audited.
- Predis / Redis session handler security (TLS, auth) — not audited; relevant if Redis is used.
- Module-internal audit posture (Weno, FaxSMS, etc.) — surface examined; internal audit not deep-dived.
- DICOM / imaging integrations (if present in modules) — not searched.
- HIPAA Privacy Rule notice/consent UX — not in code, would be operator-supplied.
- State-specific privacy laws (HITECH, 42 CFR Part 2 for SUD records, CMIA, etc.) — not assessed; specifically 42 CFR Part 2 is its own ACL category that does NOT exist in OpenEMR's `sensitivities` enumeration.
