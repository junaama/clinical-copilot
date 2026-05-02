## Parent PRD

`issues/prd.md`

## What to build

End-to-end SMART-on-FHIR standalone login flow plus the full-screen UI shell that replaces today's floating panel. After this slice ships, a clinician navigates to the copilot-ui URL, clicks "Log in," authenticates against OpenEMR's authorize endpoint, and lands back in a full-screen copilot-ui that knows who they are.

This slice covers the authentication and session layer described in the PRD's *Authentication & session*, *Browser-side handoff*, and *Frontend changes (copilot-ui)* sections, plus the three Postgres tables in *Schema changes* that don't depend on conversations: `copilot_oauth_launch_state`, `copilot_session`, `copilot_token_bundle`. It also seeds the `dr_smith` non-admin user and matching `Practitioner` FHIR resource so logging in as a non-admin works on day one. CareTeam membership rows are deferred to the next slice.

The existing EHR-launch flow (`/smart/launch` and `/smart/callback`) remains wired and unchanged so the chart-sidebar embed continues to work in parallel.

## Acceptance criteria

- [x] A second OAuth client `copilot-standalone` is registered idempotently in `oauth_clients` with `client_role='user'`, the user-scoped scope string from the PRD, and the agent backend's `/auth/smart/callback` redirect URI; secret persisted to `globals` for the agent backend to read.
- [x] Existing `copilot-launcher` client (EHR-launch path) is left untouched and still functional.
- [x] Three Postgres tables created: `copilot_oauth_launch_state`, `copilot_session`, `copilot_token_bundle`. Tables are created idempotently at agent startup or via migration. *(`PostgresSessionStore` + `open_session_store(dsn)` ship the schema via `ensure_schema()`. Tested against real Postgres.)*
- [x] Agent backend exposes `GET /auth/login` (initiates PKCE redirect to OpenEMR's authorize endpoint, no `iss`/`launch` required), `GET /auth/smart/callback` (exchanges code, mints session, sets cookie, 302s to copilot-ui root), `GET /me` (200 with user info or 401), `POST /auth/logout` (revokes session, clears cookie).
- [x] Successful login sets `Set-Cookie: copilot_session=<sid>; HttpOnly; Path=/`. *(Secure and SameSite=None to be tuned for prod deployment.)*
- [x] OAuth state, PKCE verifier, session, and token bundle are persisted in Postgres (not in-process memory). Lazy expiration via `expires_at` columns; cleanup happens on read. *(`PostgresSessionStore` selected by `lifespan()` when `CHECKPOINTER_DSN` is set. `pop_launch_state` uses `DELETE … RETURNING` for atomic single-shot reads; `get_session` evicts on expiry.)*
- [x] `fhirUser` claim is parsed from the id_token. *(Mapped to oe_user_id=0 placeholder; users.uuid lookup deferred.)*
- [x] Token refresh runs server-side without user action; an in-flight chat call whose access token is expired refreshes transparently before retry. *(`SessionGateway.get_fresh_token_bundle()` consults the stored `expires_at` against a 30s skew window and POSTs `grant_type=refresh_token` via `smart.refresh_access_token` when needed; rotated bundles are persisted before return. `/chat` calls into this helper for any cookie-bound, body-token-less standalone request, so a chat turn arriving 7h59m into an 8h session still gets a live access token.)*
- [x] copilot-ui's floating `Launcher` is replaced by a full-screen `AppShell` layout. The existing `AgentPanel` continues to work as the conversation surface inside the shell (no redesign of the chat UI in this slice).
- [x] copilot-ui has routes `/login` and `/`; on boot it calls `GET /me` with `credentials: 'include'`. 401 → render login button that links to `/auth/login`. 200 → render the app body (placeholder content showing the user's display name is acceptable for this slice).
- [x] `dr_smith` user row exists in OpenEMR `users` (provider role, non-admin); a corresponding `Practitioner` FHIR resource exists with the same UUID. *(`DemoUserSeeder::ensureSeeded()` inserts the user, `users_secure` row, and `uuid_registry` mirror idempotently. FHIR `Practitioner` is derived automatically from the same uuid.)*
- [x] The existing `/smart/launch` + `/smart/callback` EHR-launch endpoints remain functional; an EHR-launch round-trip still produces a working chat session via the URL-parameter handoff.
- [x] `SessionGateway` tests cover: successful login round-trip, expired launch state, replayed/unknown state rejected, logout revocation. *(Refresh-token rotation test deferred to token-refresh implementation.)*

## Progress notes

### 2026-05-02 — commit c64fac5

Core standalone auth tracer bullet complete:
- `SessionGateway` with in-memory store, 17 unit tests
- 4 auth endpoints on server.py, 8 integration tests
- copilot-ui AppShell + LoginPage + useSession hook
- All 72 backend tests pass, 37 UI tests pass, types clean

### 2026-05-02 — copilot-standalone OAuth client registered in OpenEMR

`CopilotClientRegistration::ensureRegistered()` now registers both clients
idempotently. The new `copilot-standalone` client uses `client_role='user'`,
the user-scoped scope string (`scopeStringStandalone()`), and the agent
backend's `/auth/smart/callback` as redirect URI. Secret is mirrored into
the new `globals.copilot_oauth_standalone_client_secret` key. Bootstrap
reads the agent backend URL from a new `copilot_agent_backend_url` global
(default `http://localhost:8000`). 18 isolated tests pass on the module
suite, including 4 new tests for the standalone path; phpstan level 10
clean.

### 2026-05-02 — Postgres-backed SessionStore landed

`PostgresSessionStore` implements the same `SessionStore` protocol as the
in-memory store, with idempotent `ensure_schema()` for all three tables
(`copilot_oauth_launch_state`, `copilot_session`, `copilot_token_bundle`).
`open_session_store(dsn)` async context manager owns an
`AsyncConnectionPool` and runs schema setup before yielding, mirroring
`open_checkpointer()`. `lifespan()` selects the Postgres backend when
`CHECKPOINTER_DSN` is set and falls back to in-memory otherwise.

Replay-rejection invariant for `pop_launch_state` is enforced via
`DELETE … RETURNING` (atomic, single-shot). Session expiry is lazy on
read — expired rows are deleted at `get_session` time.

11 new integration tests in `test_postgres_session_store.py` (gated by
`COPILOT_TEST_PG_DSN`) cover schema idempotency, launch-state round-trip
and expiry, session CRUD with lazy eviction, token-bundle upsert, and
durability across store instances. All 85 unit tests pass; ruff clean
on changed files.

### 2026-05-02 — `dr_smith` demo provider seeded on module enable

`DemoUserSeeder` (mirrors `CopilotClientRegistration`'s injected-deps
shape) idempotently inserts the demo non-admin provider used by the
standalone login flow and the (forthcoming) CareTeam-gate evals. One
short-circuiting `SELECT id FROM users WHERE username='dr_smith'` runs
on each Bootstrap pass; on the cold path it issues an INSERT into
`users` (provider role: `authorized=1, active=1`, taxonomy + abook_type
populated, legacy `users.password` left empty), an INSERT into
`users_secure` with the AuthHash-hashed password (FK on the new
`users.id`), and a mirror INSERT into `uuid_registry` so the FHIR
Practitioner endpoint resolves `Practitioner/<uuid>` back to this user
without an external `populateAllMissingUuids()` pass. The FHIR
Practitioner resource itself is derived automatically by OpenEMR's FHIR
layer — no separate write needed.

Password is sourced from a new `copilot_demo_user_password` global
(default `dr_smith_pass`). Hash + uuid generation are injected as
Closures so the seeder is unit-testable without OpenEMR's runtime
hashing or random plumbing. 5 new isolated tests in
`DemoUserSeederTest` cover the early-return branch, the full insert
path (verifies users.id flows into users_secure FK and uuid_registry
table_id), the empty-hash and non-16-byte-uuid validation guards, and
the constructor's empty-password rejection. All 23 CopilotLauncher
isolated tests pass; phpcs clean on changed files; phpstan clean on
changed files (full-codebase phpstan exceeds sandbox memory budget,
4288 files at level 10).

Remaining for this issue:
1. ~~Token refresh on access-token expiry~~ — landed in the slice below
   (2026-05-02, transparent token refresh).
2. `fhirUser` → `users.id` mapping via `users.uuid` lookup — currently
   stamped as `oe_user_id=0` placeholder. Issue 002's CareTeam gate
   needs the real id, so the mapping lands as a precursor inside that
   slice rather than retrofitted here. Easiest path: a thin
   `/apis/oemr/copilot-launcher/users/by-uuid/{uuid}` endpoint
   authenticated by the user's bearer token, returning `{user_id}`.

### 2026-05-02 — Transparent token refresh for the standalone path

The remaining open AC ("Token refresh runs server-side without user
action") is now closed. The implementation is a three-layer slice that
keeps each layer's responsibility narrow and testable in isolation:

1. **`smart.refresh_access_token()`** — POSTs `grant_type=refresh_token`
   to the OAuth token endpoint with the supplied client credentials.
   Mirrors `exchange_code_for_token()`'s shape (own httpx client when
   none supplied, raises `RuntimeError` on non-200, returns the raw
   token-endpoint payload). Public clients omit `client_secret` from
   the body — OpenEMR rejects an empty credential as `invalid_client`,
   asserted via `test_refresh_access_token_omits_secret_for_public_client`.

2. **`SessionGateway.get_fresh_token_bundle(session_id, *, refresh_fn)`** —
   the gateway-level refresh policy. Loads the stored bundle, returns
   it as-is when `expires_at` is more than `DEFAULT_REFRESH_SKEW_SECONDS`
   (30s) away from now, otherwise calls `refresh_fn(refresh_token)` and
   persists the rotated bundle before returning. `refresh_fn` is the
   seam — the gateway never sees client credentials or the token
   endpoint URL, so unit tests pass simple async closures and assert
   call counts. Three behaviors that matter beyond "did it call
   refresh":
   - **Carry the old refresh token forward** when the server doesn't
     rotate (some token endpoints don't). Asserted by
     `test_get_fresh_token_bundle_preserves_old_refresh_when_not_rotated` —
     without this, the next refresh fails with `invalid_grant` because
     we'd have overwritten `rt-current` with `""`.
   - **Don't evict the stale bundle on refresh failure.** A
     `RuntimeError` from `refresh_fn` propagates, but the stored bundle
     stays. This is intentional: a transient outage shouldn't force a
     full re-login on the next try. Asserted by
     `test_get_fresh_token_bundle_propagates_refresh_failure`.
   - **Return `None` when no bundle exists at all** (rather than
     calling `refresh_fn` with an empty string). Caller treats this
     as "needs login" cleanly.

3. **`/chat` standalone branch** — when the EHR-launch SmartStores
   bundle is absent, the cookie is present, and the request body
   didn't supply an explicit `smart_access_token`, `/chat` calls
   `_resolve_fresh_standalone_bundle()` which composes the two layers:
   discovers the token endpoint via `discover_smart_endpoints`, builds
   the closure that injects the standalone client credentials, and
   delegates to `gateway.get_fresh_token_bundle`. The rotated access
   token reaches the graph as the `smart_access_token` input — the
   same input the EHR-launch path uses, so the tool layer is unaware
   of which auth flow minted the token. Refresh failures are caught
   and logged; the request continues with an empty access token so
   the FHIR layer's auth failure surfaces to the user rather than a
   500. Asserted by `test_chat_continues_when_refresh_fails`.

The EHR-launch path (`SmartStores`-keyed-by-conversation_id) was
intentionally **not** wired into refresh in this slice. The legacy
flow's `TokenBundle.expired()` already returns `None` from
`SmartStores.get_token` when the access token is past TTL, which falls
through to the same empty-token codepath as a missing bundle. A future
slice can add refresh there with the same shape — pull the
`refresh_token` off the bundle, POST `grant_type=refresh_token`, write
back. Keeping it out of this slice avoided coupling the rewrite of the
EHR-launch in-memory store to the standalone refresh logic.

Files changed:

- `agent/src/copilot/smart.py` — new `refresh_access_token()` helper
- `agent/src/copilot/session.py` — `DEFAULT_REFRESH_SKEW_SECONDS`
  constant, `SessionGateway.get_fresh_token_bundle()` with refresh-fn
  injection seam
- `agent/src/copilot/server.py` — `_resolve_fresh_standalone_bundle()`
  helper that bridges settings + discovery + gateway, and a refresh
  call in the standalone branch of `/chat`
- `agent/tests/test_smart.py` — 3 new cases (POST shape, public-client
  no-secret behavior, non-200 raise)
- `agent/tests/test_session.py` — 6 new cases (hot path, skew refresh,
  past-expiry refresh, no-rotation carry-forward, missing bundle,
  failure propagation)
- `agent/tests/test_auth_endpoints.py` — 3 new integration cases
  (chat refreshes expired bundle, chat hot-path skips refresh, chat
  continues on refresh failure)

Tests: 288 unit tests pass (was 276; +12 new) excluding the
Postgres-required files. Ruff clean on changed files (the 3 remaining
ruff errors in repo are pre-existing in code this slice didn't
touch). The 20 eval failures (`evals/test_*.py`) are the inherited
LLM/FHIR-hitting cases noted across previous slices, unrelated to
this work.

## Blocked by

None — can start immediately.

## User stories addressed

Reference by number from the parent PRD:

- User story 1
- User story 2
- User story 17
- User story 24
- User story 25 (foundational; encryption-at-rest is finalized in `issues/009-token-encryption-at-rest.md`)
- User story 27
- User story 30
