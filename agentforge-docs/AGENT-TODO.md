# Agent Workflow — TODO Ledger

Canonical backlog for the Co-Pilot service. Add new items at the top of the
relevant section. Mark as `done:` with a date when shipped. Anything still in
in-code `TODO:` comments should be mirrored here.

Cross-reference: [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Now (in-flight)

(empty — all in-flight items shipped this session)

## Recently shipped

- [x] 2026-04-30: Replaced echo node with `langchain.agents.create_agent`
- [x] 2026-04-30: UC-2 tool surface (7 tools) over fixture FHIR
- [x] 2026-04-30: Provider abstraction (`LLM_PROVIDER=openai|anthropic`)
- [x] 2026-04-30: **Verifier node** — deterministic citation-resolution check, up to 2 regenerations with feedback, `refused_unsourced` on exhaustion
- [x] 2026-04-30: Eval runner drives `build_graph` so verifier loop is exercised in CI
- [x] 2026-04-30: Sentinel-wrap free text inputs (§9 step 7) + clarified prompt rule
- [x] 2026-04-30: Patient-context guard at tool layer (§7) + adversarial smuggling case
- [x] 2026-04-30: Classifier + clarify nodes; W-1/W-10 → triage_node, others → agent_node
- [x] 2026-04-30: UC-1 triage two-stage flow with ranking-by-significance prompt
- [x] 2026-04-30: 12/12 §8 tools live (orders, imaging, MAR added)
- [x] 2026-04-30: MedicationAdministration + MedicationRequest lifecycle canonicalization
- [x] 2026-04-30: httpx retry/backoff on transient errors
- [x] 2026-04-30: agent_audit JSONL log
- [x] 2026-04-30: Eval coverage at 16/16 — 5 smoke, 5 golden, 6 adversarial (was 8 total)

## Auth & SMART

- [ ] Register Co-Pilot as confidential SMART client in OpenEMR admin UI
- [ ] Implement `/smart/launch` — PKCE, state, redirect to authorize endpoint
- [ ] Implement `/smart/callback` — code-for-token exchange, bind to conversation
- [x] 2026-04-30: Token-context middleware: tool-layer guard validates `patient_id` against active context via contextvar; mismatch → hard-deny + decision=denied_authz; covered by unit tests + adversarial-authescape-002-id-smuggling
- [ ] Break-glass UX + audit (§14)
- [ ] Sensitive-encounter filter at retriever (§14)
- [ ] `Consent` resource enforcement when OpenEMR support matures (§14 roadmap)

## Workflow nodes

- [x] 2026-04-30: Classifier node — structured-output workflow_id + confidence; routes to clarify below 0.8
- [ ] Tool planner node (Sonnet) — fan-out to typed tool calls
- [ ] Synthesis node (Opus) — citations inline per §12.5
- [x] Verifier node — deterministic citation-resolution check
- [x] 2026-04-30: Clarify node — used when classifier confidence < 0.8 or workflow=unclear
- [x] 2026-04-30: UC-1 triage two-stage flow (§10): change-signal probe → flag-and-rank, separate `triage_node` branch with own system prompt; covered by smoke-004

## Tools (12 total per §8)

- [x] `get_patient_demographics(patient_id)`
- [x] 2026-04-30: `get_my_patient_list()` — fixture panel today; real care-team query when SMART lands
- [ ] `get_active_problems(patient_id)`
- [ ] `get_active_medications(patient_id)`
- [ ] `get_recent_vitals(patient_id, hours)`
- [ ] `get_recent_labs(patient_id, hours)`
- [ ] `get_recent_encounters(patient_id, hours)`
- [ ] `get_clinical_notes(patient_id, hours, document_types?)`
- [x] 2026-04-30: `get_recent_orders(patient_id, hours)` — ServiceRequest
- [x] 2026-04-30: `get_imaging_results(patient_id, hours)` — DiagnosticReport (radiology)
- [x] 2026-04-30: `get_medication_administrations(patient_id, hours)` — MedicationAdministration with lifecycle_status canonicalization (given/held/in-progress/stopped/voided)
- [x] 2026-04-30: `get_change_signal(patient_id, since)` — Stage-1 of UC-1, 4-channel count probe with patient-context guard

## Data layer

- [x] 2026-04-30: FHIR client with retry/backoff — httpx AsyncHTTPTransport(retries=3) on transient/5xx; 4xx surface immediately
- [ ] Field allowlist + length caps + identifier suppression (§15)
- [x] 2026-04-30: Status canonicalization for MedicationAdministration (§9 step 8: lifecycle_status). MedicationRequest validity-period canonicalization still open.
- [x] 2026-04-30: Sentinel-wrapping for patient-authored free text (§9 step 7) — note bodies + observation notes wrapped in `<patient-text id=...>`; adversarial-injection-002-poisoned-note green
- [ ] Absence-marker insertion for expected-but-missing fields (§11)

## Persistence & ops

- [ ] Postgres LangGraph checkpointer in production deploy
- [x] 2026-04-30: `agent_audit` JSONL log: per-turn decision/workflow/confidence/regens/tool-calls/fetched-refs (§9 step 11). Postgres swap is a write-target change.
- [ ] Encrypted prompts/responses table with tiered retention
- [x] 2026-04-30: Langfuse runtime callback handler wired into /chat, run_query, eval runner — no-op when env unset; per-turn trace tree on dashboard when configured

## Eval

- [ ] Smoke (5–10 cases, every push)
- [ ] Golden (25–50 cases) — UC-1, UC-2 with required claims + expected citations
- [ ] Adversarial (30+) — prompt injection, authz escapes, value-misread, omission, negation
- [ ] Drift (~15 stable cases) — re-run on every model bump

## Synthetic data

- [ ] Synthea generate → import into OpenEMR FHIR for the demo
- [ ] Fixture patient bundle for offline tests (used today before SMART OAuth lands)

## Demo / submission

- [ ] Wire chat UI iframe inside OpenEMR chart sidebar
- [ ] Loom recording — UC-2 brief end-to-end
- [ ] Move `agentforge-docs/` files to repo root before final submission

## Done

- [x] 2026-04-30: scaffold `agent/` (uv, FastAPI, LangGraph, MemorySaver)
- [x] 2026-04-30: `get_patient_demographics` tool with absence markers
- [x] 2026-04-30: smoke tests (graph compile + checkpointer persistence)
