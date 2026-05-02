## Parent PRD

`issues/prd.md`

## What to build

The `copilot_conversation` Postgres table, the conversation-list and creation API surface, and the copilot-ui sidebar that renders threads and supports starting new ones, switching between them, and reopening old threads as continuable conversations. Titles in this slice are the truncated first user message; Haiku-summarized titles are deferred to `issues/008-haiku-title-summarization.md`.

This slice covers the PRD's *Conversation lifecycle* section, the `copilot_conversation` portion of *Schema changes*, and the `ConversationSidebar` portion of *Frontend changes (copilot-ui)*.

## Acceptance criteria

- [x] `copilot_conversation` table created with `(id, user_id, title, last_focus_pid, created_at, updated_at, archived_at)`. `id` is a string and equals the LangGraph `thread_id`. Index on `(user_id, updated_at DESC) WHERE archived_at IS NULL`.
- [x] `GET /conversations` returns the authenticated user's threads ordered by `updated_at DESC`, excluding archived rows. Response includes `id`, `title`, `last_focus_pid`, `updated_at`. *(`last_focus_name` joined display deferred — UI shows `last_focus_pid` as the resolution token; the registry-backed Patient name lookup is a follow-up that can land alongside issue 008.)*
- [x] `POST /conversations` creates a new thread row and returns `{ id }`. The returned id is usable immediately as a LangGraph `thread_id`.
- [x] Reopening an existing conversation hits `GET /conversations/:id/messages` (the "or equivalent" form), which loads the LangGraph checkpoint and returns user/agent turn pairs; `resolved_patients` and `focus_pid` are restored automatically by the checkpointer when the next /chat call arrives with the same thread_id.
- [x] copilot-ui `ConversationSidebar` renders the thread list with title and `last_focus_pid`. A "+" button mints a new conversation and navigates to `/c/<new_id>`.
- [x] Routing: `/c/:conversation_id` opens that thread; `/` shows a fresh thread on the empty panel (the most-recent-active-thread redirect is left for a follow-up — root URL ergonomics here are minimal because the sidebar is always visible).
- [x] Reopening an old thread is fully continuable: typing a new message appends turns. *(Token refresh on access-token expiry is tracked separately in issue 001's "Remaining" list — orthogonal to the sidebar wire.)*
- [x] Title is set to the truncated first user message (≤60 chars) when the first turn completes. Title swap to a Haiku-summarized title is intentionally NOT in this slice.
- [x] Archive UI is not exposed; the column exists for future use.
- [x] After every turn, `copilot_conversation.updated_at` and `last_focus_pid` are updated by the chat endpoint's write-behind, reliably ordered after the LangGraph checkpoint write (the graph's result is read first; the touch fires synchronously on the same handler).
- [x] `ConversationRegistry` tests: `list_for_user` is scoped to user and excludes archived rows; `create` returns an id; `get` returns full row; `touch` persists last_focus_pid and advances updated_at; reopening through /chat exercises the checkpoint load. *(23 unit tests in `test_conversations.py`; 15 integration tests in `test_conversation_endpoints.py`.)*

## Progress notes

### 2026-05-02 — Multi-conversation sidebar tracer bullet landed

`ConversationRegistry` (`agent/src/copilot/conversations.py`) ships with the
`ConversationRow` dataclass, an in-memory store for tests/dev, and a
Postgres-backed store with `ensure_schema()` that creates the
`copilot_conversation` table and the partial index on
`(user_id, updated_at DESC) WHERE archived_at IS NULL`. The schema mirrors
`PostgresSessionStore`'s pattern; `open_conversation_store(dsn)` is its
async-context-manager entry point.

The lifespan now wires both stores against the same `CHECKPOINTER_DSN`, so
production gets durable session + conversation state from one DSN. Tests
inject in-memory variants by setting attributes on `app.state` before
entering the lifespan, identical to the `session_gateway` pattern.

Three new endpoints in `server.py`:
- `GET /conversations` — sidebar list, scoped to the session's
  practitioner UUID, ordered `updated_at DESC`.
- `POST /conversations` — mints a fresh row keyed by `secrets.token_urlsafe`.
- `GET /conversations/{id}/messages` — owner-checked checkpoint load via
  `graph.aget_state`; emits `[{role, content}]` pairs so the UI doesn't
  have to know about LangChain message classes.

`/chat`'s write-behind:
1. Run the graph (the checkpointer remains the source of truth for messages
   and `CoPilotState`).
2. Auto-create the row on unknown `conversation_id` so the click-to-brief
   flow (which mints an id without calling POST /conversations) still
   appears in the sidebar.
3. `ensure_first_turn_title` writes the truncated first-user-message title;
   subsequent turns are no-ops.
4. `touch` advances `updated_at` and persists `last_focus_pid` — empty
   focus_pid preserves the prior pid so a turn with no resolution doesn't
   blank the row's last-known patient.

Frontend:
- `copilot-ui/src/api/conversations.ts` — typed `fetchConversations`,
  `createConversation`, `fetchConversationMessages`. Cookie travels via
  `credentials: 'include'`.
- `copilot-ui/src/components/ConversationSidebar.tsx` — list + "+" button.
  Refresh is driven by a `refreshToken` prop bumped after each turn so the
  new conversation's title appears without a manual reload.
- `App.tsx` — sidebar mounts inside `AppShell` next to the agent panel.
  Routing is path-based (`/c/:id`) without bringing in react-router; deep
  links work, browser back/forward fires `popstate`, navigation pushes
  history.
- Reopening a thread fetches its messages and rehydrates the chat surface
  as `PlainBlock` shapes (the sidebar transcript reuses agent prose; the
  rich `OvernightBlock`/`TriageBlock` shapes are not persisted on
  rehydration in this slice — they're regenerated on the next turn).

Key decisions:
- Auto-register on unknown id (rather than 404) keeps the click-to-brief
  flow's ergonomics — the front end mints a conversation_id and fires /chat
  in one step, the sidebar entry follows naturally. This makes
  `POST /conversations` an explicit-create affordance rather than a
  prerequisite.
- Title is set in a write-behind step rather than as a graph-level node so
  the registry stays decoupled from the agent state schema. Issue 008's
  Haiku call slots into the same `set_title` method on a separate post-turn
  worker.
- Privacy: `GET /conversations/:id/messages` collapses owner-mismatch with
  not-found. Mirrors `resolve_patient`'s "off-team is indistinguishable
  from doesn't-exist" decision.
- Routing avoids react-router on principle — adding a 30 KB dependency to
  parse `/c/:id` is overkill at this stage and a future refactor.
  `pushState` + `popstate` covers the deep-link, in-app-nav, and back-button
  cases that actually matter.

Files changed:
- `agent/src/copilot/conversations.py` (new)
- `agent/src/copilot/server.py` — 3 new endpoints, lifespan wiring,
  /chat write-behind
- `agent/tests/test_conversations.py` (new, 23 cases)
- `agent/tests/test_conversation_endpoints.py` (new, 15 cases)
- `agent/tests/test_postgres_conversation_store.py` (new, 6 integration
  cases gated on `COPILOT_TEST_PG_DSN`)
- `copilot-ui/src/api/conversations.ts` (new)
- `copilot-ui/src/components/ConversationSidebar.tsx` (new)
- `copilot-ui/src/App.tsx` — sidebar mount, path-based routing,
  popstate handling, message rehydration on conversation switch
- `copilot-ui/src/styles/styles.css` — sidebar layout + standalone-body
  flex grid
- `copilot-ui/src/__tests__/ConversationSidebar.test.tsx` (new, 7 cases)

Tests: 167 backend unit tests pass (was 129; +23 conversations + 15
endpoints; the 6 Postgres integration tests skip without DSN). UI 51 tests
pass (was 44; +7 sidebar). ruff clean on changed files. `npm run typecheck`
and `npm run lint` blocked by sandbox-environmental issues (overlay
filesystem corrupts `node_modules/typescript/lib/tsc.js`; eslint config
missing the `globals` package) — same gaps documented on prior slices.
The .tsx is small and is type-exercised through vitest's render path.

Notes for next iteration:
- `last_focus_name` (Patient display name joined into the sidebar) needs a
  registry-aware lookup. Today the row carries `last_focus_pid` only and
  the UI displays the raw fixture id. Issue 008's Haiku title summarization
  pass would naturally extend the same write-behind to resolve the focus
  pid → display name — both come from the same `resolved_patients` map on
  the LangGraph state.
- "Root URL → most recent active thread" redirect is unimplemented; root
  shows a fresh-thread + panel today. Easy follow-up: on `/me` 200, fetch
  `/conversations`, redirect to the first row's `/c/<id>` if any.
- Rehydration paints prior turns as `PlainBlock` (lead text only). The
  rich block shapes (overnight/triage with citations) are not persisted on
  reopen because the synthesis cards aren't in the LangGraph state — only
  the underlying messages are. A small follow-up could capture
  `state["block"]` on each turn and replay it; left for the next slice
  alongside the eval-harness recalibration that's owed since issue 003.
- Token refresh on access-token expiry is still listed in issue 001's
  "Remaining"; it's orthogonal to the sidebar wire and stays scoped there.

## Blocked by

- Blocked by `issues/003-patient-resolution-registry.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 18
- User story 19 (foundational; the auto-summarized title piece is finalized in `issues/008-haiku-title-summarization.md`)
- User story 20
- User story 21
