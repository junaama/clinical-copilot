## Parent PRD

`issues/prd.md`

## What to build

After the first turn of a conversation completes, call a Haiku-class LLM once with the first user message and the first assistant response and ask for a short title. Update `copilot_conversation.title` with the result. The sidebar reflects the new title without a full reload. After this slice ships, the conversation sidebar reads like a real chat app instead of a list of truncated first messages.

This slice covers the title-summarization portion of the PRD's *Conversation lifecycle* section. Until this slice ships, conversations show the truncated first user message (set in `issues/004-multi-conversation-sidebar.md`); after this slice, the title is upgraded to the Haiku summary as soon as it's available.

## Acceptance criteria

- [x] After the first turn of a conversation completes successfully, the agent backend invokes a Haiku-class model with: the first user message, the first assistant response, and a short instruction to produce a clinical-sidebar title (≤ 60 characters, no quotes, no trailing period preferred). *(System prompt encodes both rules; `_clean_title` strips trailing periods and wrapping single/double/smart quotes as defense-in-depth.)*
- [x] The summarizer call is **non-blocking** with respect to the chat response: the user's first turn returns normally; the title update lands shortly after via a separate write. *(`background_tasks.add_task(...)` runs the call after the response is sent.)*
- [x] On success, `copilot_conversation.title` is updated to the Haiku output (truncated to 60 chars defensively). *(`set_title` clips to `TITLE_MAX_CHARS` and `_clean_title` re-clips before the registry write.)*
- [x] On failure (timeout, error, empty output), the existing truncated-first-message title is left in place. The summarizer is **not retried** for this conversation. *(All exceptions caught and logged; no retry loop.)*
- [x] The summarizer is invoked **at most once per conversation** — exactly once after the first turn — and never again on subsequent turns. *(`ensure_first_turn_title` returns `True` only on first turn; the wire-in only schedules summarize when that flag is true. Defense in depth: the summarizer also short-circuits when the registry's title differs from the truncated first message.)*
- [x] copilot-ui `ConversationSidebar` updates the displayed title without a full page reload. *(`StandaloneApp` schedules a delayed sidebar refetch ~2.5s after each chat turn so the post-Haiku title appears without user action.)*
- [x] The Haiku call is logged as its own line item (model name, latency, success/failure) in the existing observability stream, but does NOT produce an `agent_turn_audit` row — it is not a clinical turn. *(`copilot.title_summarizer` logger emits one INFO line on success with `model=` and `latency_ms=`, one WARNING on failure. No `write_audit_event` call.)*
- [x] Tests: summarizer is called exactly once after the first turn; on failure the title remains the truncated first message; on success the DB row is updated; subsequent turns do not re-trigger the summarizer. *(12 unit tests in `test_title_summarizer.py` plus 3 integration tests in `test_conversation_endpoints.py`.)*

## Progress notes

### 2026-05-02 — Haiku title summarizer landed

`HaikuTitleSummarizer` (`agent/src/copilot/title_summarizer.py`) ships with
the four-value failure ladder (unknown id → no-op; already-summarized →
no-op; factory failure → log+return; model error → log+return) plus a
quote/period/prefix scrubber tuned for the noise Haiku-class models
commonly emit ("Brief on Eduardo." → Brief on Eduardo). Production builds
the model via `build_default_haiku_factory(api_key)`; tests pass a stub
factory so the test suite runs without `langchain-anthropic` configured.

Wire-in (`agent/src/copilot/server.py`):
- `lifespan` constructs the summarizer iff `ANTHROPIC_API_KEY` is set,
  otherwise `app.state.title_summarizer = None`. The chat path no-ops
  silently when unconfigured — the sidebar keeps its truncated-message
  placeholder. Deliberately no fall-back to the configured `LLM_PROVIDER`
  model: the issue calls for Haiku-class specifically and reusing the
  main model would silently inflate per-turn cost.
- `/chat` calls `ensure_first_turn_title`, captures its `True`/`False`
  return, and uses FastAPI's `BackgroundTasks` to schedule the summarize
  call after the response is sent. The first-turn-only gate lives at the
  registry helper layer (returns `True` exactly once); the summarizer
  itself enforces the same gate as defense in depth.

UI (`copilot-ui/src/App.tsx`):
- `StandaloneApp` schedules a second `setSidebarRefresh` bump ~2.5s after
  each chat turn. The first refresh shows the truncated placeholder; the
  delayed refresh picks up the Haiku title once the background task
  completes. Subsequent turns get a no-op refetch — cheap insurance vs.
  tracking first-turn separately, and avoids relying on a precise
  model-latency budget.

Tests: 12 unit tests in `test_title_summarizer.py` cover success path,
quote/period stripping, oversized truncation, factory/model/empty
failure paths, idempotency on already-summarized rows, and unknown-id
no-op. 3 integration tests in `test_conversation_endpoints.py` (with a
synchronous stub summarizer that uses BackgroundTasks' deterministic
ordering) cover: summarizer fires exactly once on first turn (not on
turn 2); the post-summarize title appears in the next `GET
/conversations`; chat still works when the summarizer is unconfigured.

182 unit tests pass (was 167; +12 summarizer + 3 integration) excluding
the Postgres-required files which need a DB on the sandbox; ruff clean
on changed files. UI 51 tests pass (no UI test added — the
`setSidebarRefresh` change is a small useEffect tweak; the sidebar's
own behavior is already covered by `ConversationSidebar.test.tsx`).
`npm run typecheck` and `npm run lint` blocked by sandbox-environmental
issues (overlay filesystem corrupts node_modules/typescript/lib/tsc.js;
eslint config missing the globals package) — same gaps documented on
prior slices.

Notes for next iteration:
- The model name (`claude-haiku-4-5`) is hard-coded in
  `build_default_haiku_factory`. If a Settings-driven override is
  needed for cost-tuning, plumb a `haiku_model` field through and pass
  it to the factory at construction time.
- The 2.5s delayed refresh covers Haiku's typical ~1s p95 with
  generous headroom. If real-world latency proves longer, the simplest
  upgrade is a polling refresh-on-focus or a tiny SSE channel; a flag
  check on `app.state.title_summarizer` would let the UI know whether
  to even bother polling.

## Blocked by

- ~~Blocked by `issues/004-multi-conversation-sidebar.md`~~ (issue 004 done)

## User stories addressed

Reference by number from the parent PRD:

- User story 19
