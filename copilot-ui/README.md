# OpenEMR Clinical Co-Pilot — UI

The agent panel that renders inside an iframe embedded in OpenEMR's chart sidebar. Two answer types: cohort triage ("who needs attention first?") and overnight brief ("what happened to this patient overnight?"). Citations flash chart cards on the OpenEMR side via cross-frame `postMessage`.

## Stack

Vite + React 18 + TypeScript (strict). Vitest + @testing-library/react for tests. No Tailwind, no Redux — `src/styles/styles.css` is the design system.

## Running locally

```bash
npm install
cp .env.example .env       # fill in if pointing at a remote agent
npm run dev                # http://localhost:5173
```

The dev server proxies `/api` → `http://localhost:8000` (the Python LangGraph backend in `agent/`). If the backend is not running, `POST /chat` returns 502 and the panel renders the error inline — it never crashes.

## Tests

```bash
npm run test              # one-shot
npm run test:watch        # watch mode
npm run test:coverage     # writes coverage/ — thresholds enforce 80% on api/ + AgentMsg.tsx
```

## Build

```bash
npm run build             # → dist/, ready for any static host
npm run preview           # local static preview of dist/
```

## Environment

| Var | Purpose | Default |
|---|---|---|
| `VITE_AGENT_URL` | Base URL for the agent service. Empty = relative `/chat` (uses dev proxy). | `""` |
| `VITE_FHIR_BASE` | Informational; the backend handles the actual SMART exchange. | (none) |

## SMART launch parameters

The UI reads these from the URL on first paint (query string or fragment):

| Param | Purpose |
|---|---|
| `iss` | FHIR server base URL from the SMART launch redirect. |
| `launch` | Opaque launch token (exchanged server-side for an access token). |
| `patient` / `patient_id` | Patient resource id. |
| `user` / `user_id` | OpenEMR user id. |
| `access_token` | Optional Bearer token (fragment); used in fixture/dev mode. |

In v1 the agent service holds the SMART client secret and performs the PKCE handshake; the UI just forwards `launch` + `iss` and any pre-exchanged `access_token`. See the `TODO(backend handshake)` in `src/api/smart.ts`.

## Embed-mode contract — cross-frame postMessage

Events the UI **emits** to `window.parent`:

| Event | Payload | Meaning |
|---|---|---|
| `copilot:flash-card` | `{ card: CitationCard, fhir_ref: string \| null }` | The clinician clicked a citation chip — host should flash the matching chart card. |
| `__edit_mode_available` | — | Tweaks scaffold is mounted (design-tool host protocol). |
| `__edit_mode_set_keys` | `{ edits: Record<string, string\|number\|boolean> }` | Tweak value changed. |
| `__edit_mode_dismissed` | — | Tweaks panel closed by user. |

Events the UI **consumes** from `window.parent`:

| Event | Effect |
|---|---|
| `__activate_edit_mode` | Opens the Tweaks panel. |
| `__deactivate_edit_mode` | Closes the Tweaks panel. |

`CitationCard` values are the closed set defined in `src/api/types.ts`: `vitals | labs | medications | problems | allergies | prescriptions | encounters | documents | other`.

## Pointing at a different backend

Easiest: set `VITE_AGENT_URL` and rebuild.

```bash
echo 'VITE_AGENT_URL=https://copilot-agent.example.com' > .env
npm run build
```

For dev against a remote backend, change the `proxy.target` in `vite.config.ts` or set `VITE_AGENT_URL` and reload.

## Wire contract

The single source of truth is `agentforge-docs/CHAT-API-CONTRACT.md`. The TypeScript mirror is `src/api/types.ts` — every value flowing in from `/chat` is parsed through `parseChatResponse`, which throws on any drift.

## File layout

```
src/
  main.tsx                   # ReactDOM.createRoot
  App.tsx                    # SMART parse, ⌘K, Tweaks wiring, citation flash
  api/
    client.ts                # POST /chat
    smart.ts                 # parseSmartLaunch + (TODO) PKCE exchange
    types.ts                 # wire contract — Block | TriageBlock | OvernightBlock | PlainBlock
  components/
    AgentPanel.tsx           # transcript, input, /chat orchestration
    AgentMsg.tsx              # block dispatcher (the testable boundary)
    Welcome.tsx UserMsg.tsx
    Lead.tsx                  # typewriter
    CohortBlock.tsx ScoreBar.tsx
    DeltaGrid.tsx Timeline.tsx
    Thinking.tsx Launcher.tsx
    Tweaks/
      TweaksPanel.tsx         # host edit-mode protocol shell
      controls.tsx            # TweakRadio TweakToggle TweakColor TweakButton TweakSection
      useTweaks.ts
  styles/styles.css           # design system, ported verbatim from prototype
  fixtures/mockData.ts        # tests only — DO NOT IMPORT FROM RUNTIME
  __tests__/                  # vitest specs
```
