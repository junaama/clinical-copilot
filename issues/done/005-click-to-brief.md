## Parent PRD

`issues/prd.md`

## What to build

Clicking a patient on the empty-state CareTeam panel injects a synthetic `"Give me a brief on <patient name>."` user message into the active (or new) conversation. The message is rendered as a normal user turn in the chat history and goes through the same chat pipeline as a typed message. After this slice ships, the most common rounding action is one click and produces the same output as if the clinician had typed the message themselves.

This slice covers the PRD's *Empty state & click-to-brief* section. The composite tool that makes the brief fast lands in `issues/006-per-patient-brief-composite.md`; this slice works correctly on top of the existing granular tools, just slower.

## Acceptance criteria

- [x] copilot-ui `PanelView` items wire a click handler that injects the message `"Give me a brief on <given> <family>."` into the conversation as a normal user turn.
- [x] The synthetic message is **visibly rendered** in the chat history as a user message; it is not hidden, not styled differently, and not collapsed. *(`UserMsg` renders the click-injected text without the `auto` tag.)*
- [x] The agent backend handles this message via the existing `/chat` endpoint with no new endpoint, no special flag, no branch.
- [x] `resolve_patient` is a cache hit because the panel render pre-populates the conversation registry with the user's CareTeam roster. *(Server-side seed: `_seed_panel_registry` in `server.py` populates `inputs["resolved_patients"]` from `gate.list_panel(user_id)` for the standalone path; the LangGraph reducer is right-wins so the seed merges with previously-resolved entries without erasing them.)*
- [x] If the conversation has zero turns when the click happens, the panel disappears and the chat surface displays the synthetic turn followed by the assistant response.
- [x] If the click happens in an existing conversation that already has turns, a fresh conversation is created (not appended to the current thread), the user navigates to it, and the click-injected message is the first turn of the new thread. *(Implementation: `handlePatientClick` mints a fresh `conversationId` and clears `messages` when `messages.length > 0`. UI exposure of this branch is gated on the conversation sidebar — issue 004.)*
- [x] The audit row produced by the synthetic turn is shape-identical to a typed-message turn: same fields, same `extra.gate_decisions` array, same workflow classification. *(Both flows go through the same `/chat` → graph → audit pipeline; covered by `test_audit_row_shape_is_identical_for_click_and_typed`.)*
- [x] Tests: graph integration test asserts the audit row from a click-injected message matches the audit row from a typed equivalent in shape. *(See `agent/tests/test_click_to_brief.py` — 4 tests covering the registry seed, the EHR-launch no-op, and audit-shape parity. UI tests in `copilot-ui/src/__tests__/clickToBrief.test.tsx` — 3 tests covering the click → synthetic-message → /chat round-trip.)*

## Progress notes

### 2026-05-02 — Click-to-brief landed

`StandaloneApp` (`copilot-ui/src/App.tsx`) now owns `pendingMessage` and a
`handlePatientClick` callback. `AgentPanel` learned a `pendingUserMessage`
prop (`{ id, text }`) — the parent sets it with a fresh `id` to enqueue
exactly one synthetic ask; AgentPanel's effect is id-deduped via a ref
so re-renders never double-fire. Click-injected messages render as normal
user turns (no `auto` tag) per the AC.

Server-side, `/chat` calls a new `_seed_panel_registry` helper before
invoking the graph. For standalone-path requests (session cookie, no
SMART bundle), it asks the gate for the user's CareTeam panel and
seeds `inputs["resolved_patients"]` with the roster. The LangGraph
reducer is right-wins so the seed merges with previously-resolved
entries (e.g., admin-bypass resolutions outside the panel) without
erasing them. EHR-launch flow is a no-op — it stays single-patient.

`sendChat` learned `credentials: 'include'` so the session cookie
travels with /chat from the standalone shell.

Tests:
- `agent/tests/test_click_to_brief.py` (new, 4 cases) — registry-seed
  for dr_smith standalone session, no-seed when no cookie, no-seed for
  EHR-launch bundle, audit-shape parity between click-injected and
  typed turns.
- `copilot-ui/src/__tests__/clickToBrief.test.tsx` (new, 3 cases) —
  panel-click injects synthetic message and fires /chat, panel
  disappears once the conversation has a turn, /chat fires exactly
  once per click (id-dedupe).

Backend: 111 unit tests pass (was 107; +4 click_to_brief).
ruff clean on changed files. Eval-harness drift carries over from the
previous commit and is out of scope (calibration owed alongside the
composite tools in issues 006/007).

UI: 44 tests pass (was 41; +3 click-to-brief). `npm run typecheck` and
`npm run lint` blocked by sandbox-environmental issues (overlay
filesystem corrupts `node_modules/typescript/lib/tsc.js`; eslint
config missing the `globals` package). Same flavor of sandbox gap
noted on the previous slices.

Remaining for this issue: none. The "fresh thread on click" branch is
implemented but only becomes user-exercisable once the conversation
sidebar lands (issue 004).

## Blocked by

- Blocked by `issues/002-careteam-gate-panel.md`
- Blocked by `issues/003-patient-resolution-registry.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 4
- User story 28
