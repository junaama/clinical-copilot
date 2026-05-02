## Parent PRD

`issues/prd.md`

## What to build

End-to-end SMART-on-FHIR standalone login flow plus the full-screen UI shell that replaces today's floating panel. After this slice ships, a clinician navigates to the copilot-ui URL, clicks "Log in," authenticates against OpenEMR's authorize endpoint, and lands back in a full-screen copilot-ui that knows who they are.

This slice covers the authentication and session layer described in the PRD's *Authentication & session*, *Browser-side handoff*, and *Frontend changes (copilot-ui)* sections, plus the three Postgres tables in *Schema changes* that don't depend on conversations: `copilot_oauth_launch_state`, `copilot_session`, `copilot_token_bundle`. It also seeds the `dr_smith` non-admin user and matching `Practitioner` FHIR resource so logging in as a non-admin works on day one. CareTeam membership rows are deferred to the next slice.

The existing EHR-launch flow (`/smart/launch` and `/smart/callback`) remains wired and unchanged so the chart-sidebar embed continues to work in parallel.

## Acceptance criteria

- [x] A second OAuth client `copilot-standalone` is registered idempotently in `oauth_clients` with `client_role='user'`, the user-scoped scope string from the PRD, and the agent backend's `/auth/smart/callback` redirect URI; secret persisted to `globals` for the agent backend to read.
- [x] Existing `copilot-launcher` client (EHR-launch path) is left untouched and still functional.
- [ ] Three Postgres tables created: `copilot_oauth_launch_state`, `copilot_session`, `copilot_token_bundle`. Tables are created idempotently at agent startup or via migration. *(In-memory store done; Postgres store deferred to DSN wiring pass.)*
- [x] Agent backend exposes `GET /auth/login` (initiates PKCE redirect to OpenEMR's authorize endpoint, no `iss`/`launch` required), `GET /auth/smart/callback` (exchanges code, mints session, sets cookie, 302s to copilot-ui root), `GET /me` (200 with user info or 401), `POST /auth/logout` (revokes session, clears cookie).
- [x] Successful login sets `Set-Cookie: copilot_session=<sid>; HttpOnly; Path=/`. *(Secure and SameSite=None to be tuned for prod deployment.)*
- [ ] OAuth state, PKCE verifier, session, and token bundle are persisted in Postgres (not in-process memory). Lazy expiration via `expires_at` columns; cleanup happens on read. *(In-memory with expires_at lazy eviction done; Postgres persistence deferred.)*
- [x] `fhirUser` claim is parsed from the id_token. *(Mapped to oe_user_id=0 placeholder; users.uuid lookup deferred.)*
- [ ] Token refresh runs server-side without user action; an in-flight chat call whose access token is expired refreshes transparently before retry.
- [x] copilot-ui's floating `Launcher` is replaced by a full-screen `AppShell` layout. The existing `AgentPanel` continues to work as the conversation surface inside the shell (no redesign of the chat UI in this slice).
- [x] copilot-ui has routes `/login` and `/`; on boot it calls `GET /me` with `credentials: 'include'`. 401 → render login button that links to `/auth/login`. 200 → render the app body (placeholder content showing the user's display name is acceptable for this slice).
- [ ] `dr_smith` user row exists in OpenEMR `users` (provider role, non-admin); a corresponding `Practitioner` FHIR resource exists with the same UUID.
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

Remaining for this issue:
1. Postgres-backed `SessionStore` (CREATE TABLE IF NOT EXISTS)
2. `dr_smith` user + Practitioner seed
3. Token refresh on access-token expiry
4. fhirUser → users.id mapping via users.uuid lookup

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
