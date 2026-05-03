
## Things I learned & hard engineering problems

### 1. Custom REST data API → SMART on FHIR — the architecture pivot

**What I tried:** an `/api/copilot/tools/*` REST namespace inside the OpenEMR fork that hits OpenEMR's internal `*Service` classes directly. Latency-optimal because it skips the FHIR layer's known overhead (RIGHT JOINs against `patient_data`, N+1 reads in `ProcedureService`, no LIMIT on `EncounterService::search` per `AUDIT.md` §2).

**Why it was wrong:** every custom endpoint is a permanent fork divergence. The minute I needed a second resource (allergies on top of meds), I was reinventing FHIR. The architecture interview pushed back on portability: a custom API hard-codes the agent to one EHR; SMART on FHIR makes it pointable at Epic / Cerner with no code change, only a different OAuth issuer.

**What landed:** rewrote as a SMART on FHIR app reading through OpenEMR's existing `/fhir/*` endpoints. Latency mitigations move from "go around it" to "fix it" — agent-side caching, `_include` batching, upstream PRs.

**Lesson:** local performance optimizations that take you off the ecosystem's standard path are almost always wrong over a 12-month horizon. The cost of forfeiting standards compliance, community support, and portability dwarfs the latency you saved.

### 2. OpenEMR's auth model didn't match the threat model

**What I needed:** restrict the agent so it can only read records for patients on the current physician's care team.

**Why it was hard:** OpenEMR's authorization is role-based ACL — once a clinician logs in, the system trusts them to navigate to any patient. There's no native "is this user on this patient's care team?" enforcement point. The framework's auth model was designed for a different threat model than an agent service's.

**What landed:** pushed authorization out of OpenEMR entirely. The agent service runs its own care-team check before every FHIR fetch using the OpenEMR OAuth access token's bound patient ID plus a `CareTeam?participant=Practitioner/{id}` query. Defense-in-depth at the `/chat` API boundary: if the patient ID in the request doesn't match the patient bound to the OAuth token, return 403 (`agent/src/copilot/server.py`).

**Lesson:** when the framework's auth model doesn't match your threat model, don't bend the framework — put the enforcement at the boundary you actually own.

### 3. Bulk WRITE via FHIR is a dead end — runtime agent ↔ seed loader split

**What I needed:** programmatically POST FHIR resources to seed synthetic patient data before each demo.

**Why it was hard:** OpenEMR's OAuth implementation is built for two patterns — SMART Bulk FHIR (read-only system tokens) and interactive SMART apps (clinician-approved user tokens). There is no documented or working pattern for non-interactive server-to-server writes:

- `client_credentials` is hard-coded for SMART Bulk FHIR — read-only by design (`CustomClientCredentialsGrant.php`).
- `password` grant issues tokens but doesn't create the `TrustedUser` record the resource server requires (`BearerTokenAuthorizationStrategy.php:169`), so every call returns 401.
- `authorization_code` works for reads, but `user/<Resource>.write` scopes get silently filtered unless they're on the registered client, and the deployed instance doesn't issue refresh tokens.

**What landed:** I'd been trying to satisfy two operationally distinct systems with one auth pattern. The runtime agent (production, online, frequent) reads patient data via the SMART EHR-launch token. The seed loader (offline, one-off, run from a developer laptop) is an entirely separate code path: `railway ssh` into the container and run OpenEMR's bundled `importRandomPatients` shell function, which uses Synthea + the internal CCDA importer with `--isDev=true`. One shell command, ~10 sec/patient, populates `patient_data` directly. `ARCHITECTURE.md` was updated to reflect this separation: agent runtime is read-only by design, seed loader is a write-capable offline tool that touches a different surface.

**Lesson:** two operationally different lifecycles deserve two different auth strategies — sharing one is rarely worth the coupling cost. And: when the platform's auth model fights you for hours, it's usually because the platform was designed for a different shape of client than you're building.

### 4. Edge-terminated TLS makes services lie about themselves

**What I was doing:** signing JWT client assertions to authenticate against `/oauth2/default/token`.

**Why it was hard:** Railway terminates TLS at its edge proxy and forwards plain HTTP upstream. So the deployment is reachable at `https://openemr-production-c5b4.up.railway.app`, but OpenEMR thinks of itself as `http://...` because that's how the request arrives at PHP. JWT validation requires the `aud` claim to match the issuer's self-perceived URL exactly — different schemes are a hard reject. Two distinct errors hit the same root cause: `Aud parameter did not match authorized server` on authorize, `invalid_client` on token.

**What landed:** fetch OpenID and SMART discovery documents (`/.well-known/openid-configuration`, `/.well-known/smart-configuration`) at startup and use whichever URL the server self-advertises as `aud`, verbatim, no normalization. The script POSTs to the public `https://` URL but signs JWTs claiming the `http://` audience because that's what the server validates against.

**Lesson:** whenever an L7 proxy sits in front of an app, the app's idea of its own hostname is suspect. Never construct identifiers; always discover them. (See also: this is the same pattern that made the **scope vocabulary** problem solvable — `scopes_supported` on the discovery endpoint is the only ground truth for what the server will accept; docs describe design, the running instance describes runtime, and the source explains which globals gate which.)

### 5. SMART launch failures across three layers

**What I was doing:** getting the SMART EHR-launch flow working end-to-end from the OpenEMR chart sidebar into the agent.

**Why it was hard:** three distinct failures that all looked the same in the browser console:

1. **Mixed Content blocker** — `site_addr_oath` global was `http://` while the deployed site was `https://`, so the OAuth redirect was blocked.
2. **`error=invalid_scope`** — requesting `patient/*.read` (SMART v2 wildcard) against a server whose scope vocabulary only enumerates per-resource scopes.
3. **`client_role` cascade of 401s** — OpenEMR pairs `role` with scope shape. `user` role expects `user/*.read` scopes; `patient` role expects `patient/*.read`. Mismatched role + scope means tokens mint but every protected call 401s except `Patient/{id}` which slips through.

**What I tried that didn't work:** treating it as one CORS issue (CORS was fine; the redirect was the problem); adding more scopes to the request (made it worse — invalid scope is fail-closed, so adding any unknown scope rejects the whole bundle); writing `client_role = 'users'` (plural) instead of `'user'` (OpenEMR's auth handler treated 'users' as invalid and rejected every scope at token-issuance).

**What landed:** strip failures one layer at a time — transport (`site_addr_oath` to `https://`), discovery (replace wildcard with the explicit per-resource list in `agent/src/copilot/config.py`), client state (`UPDATE oauth_clients SET client_role='patient' WHERE client_id='…'` against MariaDB).

**Lesson:** "OAuth doesn't work" is rarely one bug. Strip the failures one layer at a time — transport → discovery → scope → client state — instead of guessing which layer is wrong. Auth systems often have role-scope coupling that isn't documented next to either field; when 4xx hits every protected endpoint, suspect the role, not the scope list.

### 6. The fixture fallback that became a production footgun

**What I built:** `FhirClient` had a "convenient default" — when no SMART token was bound, fall back to canned Synthea bundles.

**Why it was hard:** "convenient default for dev" silently became "fabricates synthetic data in production." The agent in prod was returning briefs based on Synthea fixtures rather than the real chart, and the only signal was that patient names in the responses were vaguely too literary. No error, no log line, no test failure — just wrong answers from a healthy-looking system. In a clinical agent, that's the exact failure mode the entire architecture exists to prevent.

**What I tried:** added `USE_FIXTURE_FHIR=0` env var with default `True`. Default-on means any environment that forgets to set it serves fixtures. Production forgot.

**What landed:** flipped the default to `False`. Removed the implicit fallback in `FhirClient` entirely — both `search()` and `read()` now return an explicit `error="no_token"` if no token is bound, and the synthesizer surfaces a refusal message instead of guessing (`agent/src/copilot/fhir.py`). Replaced the test that asserted the fallback with one that asserts the refusal.

**Lesson:** defaults that make development convenient are exactly the defaults that make production dangerous. The invariant should fail loud at every layer, not silently degrade.

### 7. A migration that "succeeded" but did nothing

**What I was doing:** verifying the CCDA-import pipeline by running it with 3 test patients before scaling up.

**Why it was hard:** the first run logged `System has successfully imported CCDA number: 1/2/3` and `Completed run for following number of random patients: 3` — every line said "imported." But `SELECT COUNT(*) FROM patient_data` returned 0. The CCDAs landed in the `documents` table with `foreign_id=0` (unlinked), waiting for an admin to manually walk through OpenEMR's UI and click "Match patient or create new" for each one. The behavior is gated by an `isDev` flag on `import_ccda.php` that I'd passed as `false` without thinking.

**What landed:** read the source of `importRandomPatients` in `/root/devtoolsLibrary.source:234`, re-ran with `isDev=true` — which bypasses the manual-review queue and creates `patient_data` rows directly. 119 encounters, 128 list items, 19 prescriptions, 3 vitals appeared per patient.

**Lesson:** when verifying a bulk-import, count the rows you expected to land. Don't trust the importer's self-report. The script's success criteria might be "I parsed your input"; yours is "you created the records." Same word, different meanings.

### 8. The PHP module wasn't actually deploying

**What I built:** `oe-module-copilot-launcher` — bootstrap, listeners, audit endpoint, embed page, isolated tests. PHPStan green at level 10. PHPUnit green.

**Why it was hard:** a friend's-eye review of the running prod surface revealed the module didn't exist there. The Railway `openemr` service was pulling `openemr/openemr:latest` from Docker Hub directly; my PHP files lived only in the git repo. I'd built and tested it for days under the assumption it was deployed.

**What landed:** [`docker/openemr-railway/`](docker/openemr-railway/) — a Dockerfile that bases on the upstream image and `COPY`s the module in, plus a `build.sh` that stages the module into the build context (since Railway uploads only the build context, not the parent repo).

**Lesson:** when something lives in your repo *and* you're deploying from a registry image, the gap is silent. CI green ≠ prod green. Add an end-to-end probe that asserts the deployed code path actually runs.

### 9. Single-use OAuth state that died with the process

**What I was doing:** completing the `authorization_code` browser dance from the seed-loader CLI.

**Why it was hard:** the PKCE `code_verifier` is generated client-side, hashed once into the `code_challenge`, and must be sent with the token-exchange to prove the same client started and finished the dance. I held the verifier in a Python local variable. When the user couldn't paste the ~3KB redirected URL fast enough and hit Ctrl-C, the verifier evaporated with the process. Authorization codes are also single-use with ~60-second TTLs, so the next attempt needed a fresh dance from the start.

**What landed:** persist `(verifier, state, authorize_url, issued_at)` to `secrets/pending_auth.json` before printing the authorize URL, plus `--paste-file` and `--print-url` flags so a paste failure is recoverable from disk state instead of requiring a fresh round-trip. Matches what production OAuth client libraries do.

**Lesson:** state needed to complete an interactive flow must outlive the interactive moment. If a Ctrl-C destroys it, the design assumes too much about how the user behaves.

### 10. Cross-frame postMessage as a chart-flash bridge

**What I needed:** the chat (in an iframe) flashes the chart card it cited (e.g., "vitals" when discussing BP) on the parent OpenEMR page.

**What landed:** a `copilot:flash-card` postMessage with a constrained card vocabulary (`vitals | labs | medications | problems | allergies | prescriptions | encounters | documents | other`) and explicit `event.origin` checks on the receiving side. The PHP module injects a JS bridge that maps OpenEMR section headings to card names by string matching, since OpenEMR's stock chart cards don't carry `data-card` attributes.

**Lesson:** the schema for "what can the iframe ask the parent to do" should be enumerated at design time. A free-form selector or arbitrary CSS class would be both an XSS hole and a UX cliff.

### 11. Pydantic SecretStr propagation has surface area

**What happened:** a pytest run accidentally printed the OpenAI API key to stdout via a stack trace.

**What landed:** migrating six secret fields to `SecretStr` was 8 call sites — `ChatOpenAI`/`ChatAnthropic` constructors, the FHIR client, the SMART exchange, the Langfuse client, the eval sync script — each requiring `.get_secret_value()` only at the point of network use, never logged.

**Lesson:** `SecretStr` is not a one-line change. It's a typed-throughout pattern. The places that *aren't* obvious are the ones that bite you (eval sync, observability fingerprints, ad-hoc CLI runners).

### 12. Cross-site cookies are dead on Railway — same-origin or bust

**What I built:** the standalone-login flow per the PRD — `copilot-ui` on its own Railway service, `copilot-agent` on another, agent sets `SameSite=None; Secure` on the session cookie so the UI can call `/me` cross-origin with credentials. Locally on `localhost:5173 → localhost:8000` it worked.

**Why it was hard:** in production every login looped back to the login page. The agent set the cookie correctly on its own domain (verifiable by navigating directly to `/me` — 200 with the user info), but the UI's XHR from `copilot-ui-production` to `copilot-agent-production` never sent it. Chrome's third-party-cookie protection drops cross-site cookies even with `SameSite=None; Secure` once the user has any tracking-protection mode enabled — and Railway's `*.up.railway.app` is on the Public Suffix List, so each subdomain is its own registrable site as far as the browser is concerned.

**Things I tried that didn't work:** flipping `SameSite=Lax → None`, bumping `Secure=True`, double-checking CORS `allow_credentials`. All correct, none of it mattered — the cookie was never sent in the first place.

**What landed:** make the agent serve the UI. Multi-stage Dockerfile builds `copilot-ui` with Node, copies `dist/` into the Python image, mounts it at `/` via FastAPI `StaticFiles`. One origin, no CORS credentials dance, `SameSite=Lax` (the safer default) is enough. The build context moved from `agent/` to repo root so the Dockerfile can see both `agent/` and `copilot-ui/`; a top-level `railway.toml` keeps Railway from autodetecting the OpenEMR `composer.json` and trying to build a PHP image.

**Lesson:** in 2026, "two cooperating services on subdomains" is fundamentally broken for cookie-based auth in browsers, regardless of how correct your headers are. Either share an eTLD+1 (custom domain), serve from one origin, or move to bearer tokens. Pick before you ship; retrofitting any of those is invasive.

### 13. The CareTeam gate's FHIR query the EMR doesn't support

**What I built:** a `CareTeamGate` that calls `GET /CareTeam?participant=Practitioner/{user_id}` to find a clinician's care teams. PRD spec, FHIR R4 standard, recording-stub unit tests all green.

**Why it was hard:** in production the gate fail-closed every non-admin clinician — `dr_smith` logged in, `/me` returned 200, but the panel said "you aren't a member of any CareTeam yet." The seeded `care_team_member` table had 30 rows for `dr_smith` and the FHIR Practitioner UUID resolved correctly. Took a code dive into OpenEMR's FHIR module to find the answer: `FhirCareTeamService::loadSearchParameters` only registers four params — `patient`, `status`, `_id`, `_lastUpdated`. **There's no `participant` search.** Unsupported params are silently ignored, so my query was effectively `?` — match every team I had read access to — and the client-side filter then found none.

**What landed:** pivot the search to params the EMR supports. `assert_authorized` queries `?patient={pid}&status=active` and walks `participant[].member.reference` client-side. `list_panel` queries `?status=active` and filters participants the same way. Two regression tests with a recording FHIR client assert neither code path sends `participant=` — locking the bug shut.

**Lesson:** FHIR resources advertise capabilities at runtime via `CapabilityStatement`/`smart-configuration`. "The spec supports this search param" doesn't mean "the EMR you're talking to supports it." Always verify against the running instance — same lesson as #4 (discovery over construction), in a different layer.

### 14. The five-gate login chain for a non-admin user

**What I built:** a `seed_careteam.py` script that creates `dr_smith` in the `users` table, sets a bcrypt password in `users_secure`, and links the user to ~half of seeded patients via `care_teams` + `care_team_member`. Idempotent SQL, 49 unit tests, runs fine against the deployed MariaDB.

**Why it was hard:** login still failed with "verify the information you have entered is correct." OpenEMR's audit log (`SELECT comments FROM log WHERE event='api'`, base64-decoded) walked me through five distinct gates the user has to pass, each surfacing a different failure message:

1. **`users.active = 1`** — set by the seed.
2. **`groups.user = 'dr_smith'`** — the *auth-group* table, not the ACL table. Without a row, login fails with `user not found in a group`. SQL INSERT is fine.
3. **`aclGetGroupTitles($username) > 0`** — phpGACL membership. **Direct `gacl_aro` + `gacl_groups_aro_map` INSERTs don't work** because phpGACL caches group lookups internally. Only `AclExtended::addUserAros($user, "Physicians")` over the OpenEMR PHP runtime updates everything correctly. The seed now prints a copy-pasteable PHP one-liner via `--print-acl-php` for the operator to pipe through `railway ssh`.
4. **`users_secure.password` valid** — bcrypt hash. **The prefix matters:** Python's `bcrypt` library defaults to `$2b$`, but this PHP build's `password_get_info()` returns `algoName='unknown'` for `$2b$` and `AuthHash::hashValid` rejects the hash. The seed rewrites `$2b$` → `$2y$` (byte-compatible bcrypt; PHP's preferred prefix). A round-trip-in-Python test wouldn't have caught this — Python's bcrypt accepts both prefixes; the bug is only visible at PHP's `password_get_info`.
5. **`AuthHash::passwordVerify(plaintext, hash)`** — vanilla `password_verify` once gates 1–4 pass.

**What landed:** the seed handles 1, 2, 4, 5 via SQL (with the $2y$ rewrite); 3 is a PHP one-liner the operator runs. Plus a probe loop: re-query `aclGetGroupTitles` after each fix to detect which gate is currently failing. Each "Sorry, verify the information…" message in the UI is one of these five; the audit log is what tells you which.

**Lesson:** "user can't log in" is rarely one bug, just like OAuth (#5). When the framework's auth path checks N independent invariants, you must seed all N; the diagnostic surface is the audit log, not the UI's generic error. And: when a library's default is byte-compatible-but-prefix-different from what the framework recognizes, that's a paper cut you can only find by running the verification path that ships with the framework, not the one that ships with the library.

---

## Patterns across these — what I'd watch for next time

1. **Standards vs. local optimization.** Custom REST API vs. SMART on FHIR, eval runner against bare `create_agent` vs. the full graph pipeline — every time the design optimized for short-term local wins over the ecosystem-standard path, the right answer was the standard. When designing against a framework, default to its grain even when local performance arguments push against it. Latency is recoverable through caching and batching; community support, portability, and upgrade safety are not, once you've forked.

2. **Framework defaults that don't match the threat model.** OpenEMR's role-based ACL and the fixture-fallback default both assumed a different operator than I actually had. When the framework's "obvious" answer doesn't match the constraint, push enforcement to a boundary you own — and make safe defaults fail loud, not silently degrade.

3. **Edge proxies create silent identity drift.** Anything that terminates TLS or rewrites the host means the service has two identities: how clients reach it, and how it reaches itself. JWT `aud`, OAuth issuer URLs, redirect URIs, signed cookies, deep links — all leak. In any environment with an L7 proxy, distrust constructed URLs; fetch them from a discovery endpoint at startup and round-trip the server's notion of itself.

4. **Migration / deployment "success" is a vocabulary mismatch.** The CCDA importer's `isDev=false` path "succeeded" while doing nothing useful. The PHP module's PHPUnit suite "passed" while the code wasn't deployed. The script's contract is "I parsed and queued"; yours is "the records exist and the code runs." Write a row-count assertion (or an end-to-end probe) after every bulk operation before trusting any subsequent step.

5. **Browser cookie semantics changed; design for one origin or use bearer tokens.** Cross-site cookies (#12) and the FHIR participant search (#13) are the same shape of bug: a spec / standard says X is supported, but the runtime your code sits on top of (Chrome's tracking protection; OpenEMR's `loadSearchParameters`) silently disagrees. Before relying on a cross-cutting capability, prove it works against the *deployed* runtime, not against a fixture or a unit test. For auth specifically: same-origin or `Authorization: Bearer` are the two future-proof options; "cooperating subdomains with `SameSite=None`" is no longer one of them in 2026 browsers.

6. **Framework auth paths gate on N independent invariants — seed all N, debug from the audit log.** OpenEMR's login chain checks five things (`users.active`, `groups`, `aclGetGroupTitles`, `users_secure.password`, `passwordVerify`). Each surfaces the same generic UI message. The diagnostic is the audit log (#14); the seed must satisfy every gate, not just the obvious ones. Same shape as #5 (OAuth across three layers): when "user can't log in" is the symptom, count the gates before guessing which one's broken.

---