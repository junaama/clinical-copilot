# OpenEMR Clinical Co-Pilot — System Design

A multi-turn conversational agent embedded in OpenEMR's chart workflow. It reads patient data through OpenEMR's standard FHIR R4 endpoints, synthesizes timestamped, source-cited answers across structured data and free-text notes, and serves three clinical user roles: the inpatient hospitalist, the consulting specialist, and the inpatient clinical pharmacist.

---

## 1. Requirements

### 1.1 Functional requirements

The system must support eleven workflows, grouped by user role. Each is described as: input the user supplies → expected output → primary user.

**Hospitalist workflows:**

| ID | Workflow | Input | Output | User |
|---|---|---|---|---|
| W-1 | Cross-patient morning triage | "Of my N patients, who needs attention first?" | Ranked list of 3–6 patients with one-line reasons; remainder marked stable | Hospitalist |
| W-2 | Per-patient 24-hour brief | "What happened to patient X overnight?" | Timestamped chronological event list with citations | Hospitalist |
| W-3 | Pager-driven acute context | Free-form description of a sudden change ("82/48 in 7-East") | Three-block brief: baseline, what's been tried today, reframing context | Hospitalist |
| W-4 | Cross-cover onboarding | "Walk me through the sickest patients on this service" | Ranked walkthrough with admission story, trajectory, and overnight watch items | Hospitalist (cross-cover) |
| W-5 | Family-meeting prep | "Brief me for the family meeting on bed 14" | Two-section narrative: arc-so-far + current state, plain-language | Hospitalist |
| W-6 | Causal trace | "Why has X been changing?" | Hypothesis-ranked list of correlated chart data with citations | Hospitalist, Consultant, Pharmacist |
| W-7 | Targeted drill | Series of short factual questions, each depending on the previous | Direct answers, 1–3 sentences each, cited | All roles |

**Consulting specialist workflows:**

| ID | Workflow | Input | Output | User |
|---|---|---|---|---|
| W-8 | Consult orientation | Consult reason + room number | Specialty-tuned brief: admission story, what's relevant to consult, what primary team has tried | Consultant |
| W-9 | Re-consult delta | "What changed since I was consulted last time?" | Two-section: prior recommendations + what happened with each + new relevant events | Consultant |

**Pharmacist workflows:**

| ID | Workflow | Input | Output | User |
|---|---|---|---|---|
| W-10 | Med-safety scan | "Which patients on my panel need pharmacist review today?" | Ranked list of patients with the specific concern flagging each | Pharmacist |
| W-11 | Antibiotic stewardship | "Should patient X still be on broad-spectrum coverage?" | Empiric-vs-targeted status, time on therapy, culture data, narrowing options the chart supports | Pharmacist |

**Universal functional invariants** (apply to every workflow):

- The agent supports multi-turn conversation; later turns can reference state from earlier turns in the same session.
- Every clinical claim in any response carries a citation handle that resolves to a FHIR resource fetched in the same turn.
- The agent reads only — no writes to the chart, no order entry, no auto-saving notes.
- The agent reports facts. It does not diagnose, recommend doses, or pick between differential hypotheses.
- The agent surfaces sources it checked, including for empty results ("no record found in *X, Y, Z*").

### 1.2 Non-functional requirements

| Dimension | Target | Notes |
|---|---|---|
| Response latency (p50) | <8 seconds for first response | Includes FHIR fan-out + LLM synthesis + verification |
| Response latency (p95) | <15 seconds | Tail dominated by FHIR's stock N+1 paths |
| Concurrent users | 50 at MVP, 1k at single-hospital scale, 10k at multi-hospital | See §4 |
| Availability target | 99.5% during clinical hours | Clinical assistive tool, not life-critical; chart UI must remain available even when agent is down |
| Verification rate | 100% of clinical claims must be source-grounded; unsourced claims block the response | Hard requirement, not an SLO |
| Audit completeness | Every agent decision (allow / refuse / tool failure / breakglass) has an audit row | HIPAA-relevant even on synthetic data |
| Cost per turn (target) | ≤$0.15 typical, ≤$0.50 worst-case | Sonnet/Opus token mix; see §4 cost model |
| Token efficiency | Prompt caching enabled for system prompt and persistent patient context | Cuts repeated-prompt cost ~10× |

### 1.3 Constraints

- **Single engineer, one-week MVP.** Time-to-defensible-demo is the dominant constraint.
- **OpenEMR is the system of record.** All clinical data reads go through OpenEMR; no parallel patient database.
- **Standards compliance.** FHIR R4 + SMART on FHIR; no custom data API parallel to the standard. (Architectural pivot from a prior draft after interview review.)
- **BAA-eligible vendors only** before any real-PHI deployment. Synthetic data only for MVP.
- **Existing tech stack to honor:** PHP 8.2 + MariaDB on the OpenEMR side; chosen Python + LangGraph + Anthropic on the agent side; Railway as the deploy target initially.
- **Read-only at the chart boundary.** Architectural; not optional.
- **Patient-scope authorization** must be added because OpenEMR's stock authorization is role-only (no per-patient ACL).

---

## 2. High-Level Design

### 2.1 Component diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  Hospitalist / consultant / pharmacist browser                      │
│                                                                     │
│   ┌───────────────────────────────────────────────────────────┐     │
│   │  OpenEMR chart UI                                         │     │
│   │  [ Open Co-Pilot button ] ──────────────────┐             │     │
│   └─────────────────────────────────────────────┼─────────────┘     │
│                                                 │ SMART EHR launch  │
│                                                 │ (token, patient,  │
│                                                 │  user, scopes)    │
│                                                 ▼                   │
│   ┌───────────────────────────────────────────────────────────┐     │
│   │  Co-Pilot chat iframe (served from Co-Pilot service)      │     │
│   └─────────────────────────────────────────────┬─────────────┘     │
└─────────────────────────────────────────────────┼───────────────────┘
                                                  │ POST /api/chat
                                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Co-Pilot service (Python, separate Railway deploy)                 │
│                                                                     │
│   LangGraph agent loop:                                             │
│     classifier(Haiku) → tool_planner(Sonnet) →                      │
│     tool calls (parallel FHIR GETs) → verifier →                    │
│     synthesis(Opus) → reply                                         │
│                                                                     │
│   State: Postgres (LangGraph checkpointer)                          │
│   Audit writes: agent_audit + agent_message tables in OpenEMR DB    │
└──────┬───────────────────────────────────────────┬──────────────────┘
       │                                           │
       │ FHIR R4 GETs                              │ Anthropic Messages API
       │ (SMART OAuth bearer)                      │
       │                                           ▼
       ▼                                     ┌──────────────────┐
┌─────────────────────────────────────┐      │  Anthropic API   │
│  OpenEMR + MariaDB                  │      │  Sonnet / Opus / │
│                                     │      │  Haiku           │
│  Existing FHIR R4 US Core endpoints │      └──────────────────┘
│  + new agent_audit + agent_message  │
│  + thin module: launch button only  │      ┌──────────────────┐
└─────────────────────────────────────┘      │  LangSmith       │
                                             │  (traces)        │
                                             └──────────────────┘
```

### 2.2 Data flow (single turn, happy path)

1. User clicks **Open Co-Pilot** in OpenEMR's chart sidebar.
2. OpenEMR initiates SMART EHR launch; user authenticates if needed; the Co-Pilot service receives a launch token.
3. Co-Pilot exchanges launch token for a SMART access token scoped to the launching patient + user + role-derived FHIR scopes.
4. Co-Pilot serves the chat iframe; iframe lives inside the chart window.
5. User submits a message. Browser POSTs `/api/chat` to Co-Pilot.
6. Co-Pilot loads (or initializes) the LangGraph state for this conversation from Postgres.
7. **Classifier node** decides: which workflow does this message match? Routes to the appropriate planner.
8. **Tool planner node** emits a list of FHIR queries to run in parallel.
9. **Tool calls** execute in parallel. Each calls an OpenEMR FHIR endpoint with the SMART access token. Each response is parsed into structured rows; PHI free-text fields are wrapped in sentinel tags before any LLM-bound prompt.
10. **Synthesis node** writes the response, embedding citation references inline.
11. **Verifier** parses the response. Every clinical claim must carry a citation reference resolving to a FHIR resource fetched in this turn. If any claim is unsourced, regenerate (up to 2 retries) or refuse explicitly.
12. **Audit write.** A row is added to `agent_audit` with the decision, model, token counts, latencies, and tool list. The full prompt + response is encrypted and stored in `agent_message`.
13. **LangSmith trace** flushed.
14. Response returned to browser. Chat iframe renders the response with clickable citations.

### 2.3 API contracts

**Browser → Co-Pilot service** (the only HTTP API the agent exposes):

```
POST /api/chat
Authorization: Bearer <SMART access token>
Content-Type: application/json

Request:
{
  "conversation_id": "uuid | null",
  "message": "string",
  "context": {
    "patient_id": "string | null",
    "encounter_id": "string | null",
    "user_role_hint": "hospitalist | consultant | pharmacist | null"
  }
}

Response:
{
  "conversation_id": "uuid",
  "turn_id": "uuid",
  "response_text": "string with inline <cite ref='...'/> tags",
  "citations": [
    { "ref": "Observation/abc-123", "resource_type": "Observation", "resource_id": "abc-123", "display": "BP 90/60 at 03:14" }
  ],
  "decision": "allow | refused_unsourced | refused_safety | tool_failure | denied_authz | breakglass",
  "decision_reason": "string | null",
  "trace_id": "string"
}
```

**Co-Pilot service → OpenEMR** (standard FHIR R4 calls):

```
GET /apis/{site}/fhir/Patient/{id}
GET /apis/{site}/fhir/Condition?patient={id}&clinical-status=active
GET /apis/{site}/fhir/MedicationRequest?patient={id}&status=active
GET /apis/{site}/fhir/Observation?patient={id}&category=vital-signs&date=ge{ts}
GET /apis/{site}/fhir/Observation?patient={id}&category=laboratory&date=ge{ts}
GET /apis/{site}/fhir/Encounter?patient={id}&date=ge{ts}
GET /apis/{site}/fhir/DocumentReference?patient={id}&date=ge{ts}
GET /apis/{site}/fhir/MedicationAdministration?patient={id}&effective-time=ge{ts}
```

All carry the SMART OAuth bearer. No custom OpenEMR endpoint is added for data access.

**Co-Pilot service → Anthropic** (standard Messages API with tool use):

Standard `POST /v1/messages` with tools defined per LangGraph node. Prompt caching enabled on the system prompt and on persistent patient context blocks.

### 2.4 Storage choices

| Store | Owns | Why |
|---|---|---|
| OpenEMR's MariaDB | All PHI; new `agent_audit` and `agent_message` tables | PHI stays inside OpenEMR's audit boundary. New tables extend the existing audit chain rather than fork it. |
| Postgres (Railway-managed) | LangGraph checkpointer state — node positions, conversation references | LangGraph's officially supported checkpointer backend. Holds graph state; PHI snippets that pass through prompts are persisted in `agent_message` (encrypted, in MariaDB), not in Postgres. |
| Anthropic (third-party, ephemeral) | Inflight prompts/responses during model calls | Subject to BAA; prompts/responses are not persisted by Anthropic when prompt-caching is configured for cache-only retention. |
| LangSmith (third-party) | Traces — tool calls, latencies, tokens, prompts/responses | Synthetic data only at MVP. Pre-clinical-go-live we swap to self-hosted Langfuse to drop the third-party dependency. |

---

## 3. Deep Dive

### 3.1 Data model

**`agent_audit`** — one row per agent decision. Extends OpenEMR's existing `log` table chain.

```sql
CREATE TABLE agent_audit (
  id                BIGINT PRIMARY KEY AUTO_INCREMENT,
  session_id        CHAR(36) NOT NULL,
  turn_id           CHAR(36) NOT NULL,
  user_id           BIGINT NOT NULL,
  patient_id        BIGINT NULL,
  user_role         ENUM('hospitalist','consultant','pharmacist','other') NOT NULL,
  decision          ENUM(
    'allow',
    'refused_unsourced',
    'refused_safety',
    'tool_failure',
    'blocked_baa',
    'denied_authz',
    'breakglass'
  ) NOT NULL,
  decision_reason   TEXT NULL,
  workflow_id       VARCHAR(16) NULL,        -- W-1 through W-11
  tool_calls        JSON NOT NULL,           -- [{name, latency_ms, status, citations_returned}]
  tokens_in         INT NULL,
  tokens_out        INT NULL,
  cost_estimate_usd DECIMAL(8,4) NULL,
  model_route       JSON NULL,               -- which model handled which node
  trace_id          VARCHAR(64) NULL,        -- LangSmith trace ID
  parent_log_id     BIGINT NULL,             -- FK to OpenEMR's existing log table
  created_at        DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  hash_chain        CHAR(64) NOT NULL,       -- sha256 of prior row + this row
  KEY idx_session   (session_id, turn_id),
  KEY idx_user_pt   (user_id, patient_id, created_at),
  KEY idx_decision  (decision, created_at)
);
```

**`agent_message`** — encrypted prompt/response payloads. One row per LLM message in a turn.

```sql
CREATE TABLE agent_message (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  session_id      CHAR(36) NOT NULL,
  turn_id         CHAR(36) NOT NULL,
  role            ENUM('system','user','assistant','tool') NOT NULL,
  content_enc     LONGBLOB NOT NULL,         -- AES-256-GCM, key from OpenEMR's CryptoGen
  citations       JSON NULL,                  -- list of FHIR refs cited in this message
  created_at      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY idx_session (session_id, turn_id, role)
);
```

**LangGraph checkpointer** (Postgres) — opaque to us; LangGraph manages.

### 3.2 Tool wrappers (in the Co-Pilot service)

Each tool is a Python function that maps a LangGraph tool call to one or more FHIR GETs. Output schema common to all:

```python
class ToolResult(TypedDict):
    ok: bool
    rows: list[Row]              # structured, sentinel-wrapped where free-text
    sources_checked: list[str]   # human-readable: "Observation (vital-signs)", "DocumentReference"
    error: str | None
    latency_ms: int

class Row(TypedDict):
    fhir_ref: str                # "Observation/abc-123"
    resource_type: str
    fields: dict                 # canonicalized fields, e.g. {"vital_type":"BP","value":"90/60","time":"03:14"}
    raw: dict                    # original FHIR resource subset
```

**Canonicalization rules** (applied in tool wrappers, before the LLM sees the data):

- `medication.lifecycle_status` derived from `MedicationRequest.status` + `dispenseRequest.validityPeriod.end`
- `condition.lifecycle_status` derived from `Condition.clinicalStatus` + `verificationStatus` + `abatement`
- Free-text content (DocumentReference body, Observation note) wrapped in `<patient-text id="...">…</patient-text>` sentinels
- Demographic inconsistencies surfaced explicitly (e.g., `title` vs `gender` mismatch returns `record_inconsistency: true`)

### 3.3 Caching

| Layer | What | TTL | Scope |
|---|---|---|---|
| FHIR response cache | Demographics, problem list | 30 min | Per-session |
| FHIR response cache | Vitals, labs, recent notes | 5 min | Per-session |
| FHIR resource memoization | By `{resource_type, id}` | Lifetime of one turn | Per-turn |
| Anthropic prompt cache | System prompt + persistent patient-context block | Anthropic-managed (~5 min idle) | Cross-session for shared system prompt; per-session for patient context |
| LangGraph state | Conversation graph | Until session expires (24h default) | Per-session |

No cross-session PHI cache. Patient data freshness on a new chat session always begins with a fresh FHIR fetch.

### 3.4 Queue / event design

MVP is fully synchronous. No queue between browser and Co-Pilot service; no queue between Co-Pilot and FHIR; no queue between Co-Pilot and Anthropic.

Future asynchrony triggers:
- p95 turn latency exceeds 30s consistently → fan tool calls into a worker pool with WebSocket streaming back to the browser
- Streaming token output requested → enable Anthropic streaming + verification mid-stream (requires verification rework)

### 3.5 Error handling and retry logic

| Failure | Behavior |
|---|---|
| FHIR call returns 4xx | Surface tool failure; no retry. Authorization or scope problem — retrying won't fix it. |
| FHIR call returns 5xx | Retry once with 500ms backoff. If still failing, mark tool result `ok=false`, surface to clinician with the tool name. No silent retry on patient-data tools. |
| FHIR call times out (>10s) | Treat as 5xx. |
| Anthropic 5xx or timeout | Retry up to 2 times with exponential backoff (1s, 3s). After that: return "AI temporarily unavailable" to the user. The chart UI behind it stays alive. Optional Bedrock failover if configured. |
| Anthropic safety-refuses a prompt | Surface the refusal verbatim with the provider's reason. No bypass. Audit `decision='refused_safety'`. |
| Verification rejects a response | Regenerate with a feedback message ("your previous response had unsourced claims X, Y") up to 2 times. After 2 failures, surface explicit refusal listing the unsourced claims and the sources checked. Audit `decision='refused_unsourced'`. |
| Authorization denial (user not on care team / consult / panel) | Audited refusal with "you don't have access" — never "no data," because that would leak existence. Audit `decision='denied_authz'`. |
| BAA expiry / vendor config check fails at startup | Service refuses to dispatch any chat request. Returns a service-level error to the browser. Audit `decision='blocked_baa'` for every attempt during the outage. |
| Patient genuinely has no record of the asked-about thing | Distinguish from tool failure: response includes "no record found in [Observation, DocumentReference, Encounter]" with the explicit list of sources checked. Audit `decision='allow'` with `empty_result=true`. |
| Sentinel-wrapped patient text contains injection attempt | System prompt instructs the model to treat sentinel content as untrusted data. If the model still complies, the verifier catches the unsourced or cross-patient claims and refuses. |

---

## 4. Scale and Reliability

### 4.1 Load estimation

For a 200-bed hospital with 25 hospitalists + 15 active consultants + 8 pharmacists ≈ **48 active users**:

| Window | Concurrency | Pattern |
|---|---|---|
| 7:00–7:30 AM (rush) | ~25 hospitalists | Heavy fan-out — each runs W-1 + W-2 multiple times |
| 8:00 AM–12:00 PM | ~15–20 (mix of all roles) | Mixed workflows; consults and pharmacist scans overlap with rounding |
| 12:00–18:00 PM | ~10–15 | Continued mix, declining tail |
| Evening / overnight | ~3–5 | Cross-cover hospitalist mostly |

**Per-session usage:** 5–15 turns per session, 30–60 minute session window. Per-turn: 3–5 FHIR calls in parallel + 1–3 LLM calls.

**Per-turn token model** (typical W-2 24-hour brief):

| Node | Model | Tokens in | Tokens out | Cost (Anthropic 2026 list) |
|---|---|---|---|---|
| Classifier | Haiku 4.5 | ~500 | ~50 | ~$0.001 |
| Tool planner | Sonnet 4.6 | ~3,000 | ~300 | ~$0.014 |
| Synthesis | Opus 4.7 | ~12,000 | ~1,200 | ~$0.150 |
| **Per-turn total** | | ~15,500 | ~1,550 | **~$0.165** |

W-1 (cross-patient triage) costs more — multiplies the per-patient fan-out. W-7 (targeted drill) costs less — fewer tools per turn.

### 4.2 Scaling tiers

| Tier | Architecture |
|---|---|
| **MVP / pilot, ≤100 users** | Single Co-Pilot pod on Railway + 1 Postgres + OpenEMR as-is. Anthropic direct. LangSmith. |
| **Single hospital, ≤1k users** | 3–5 Co-Pilot pods behind a load balancer. Postgres with read replica. MariaDB read replica for OpenEMR's FHIR-heavy read paths. Anthropic prompt caching enabled (≈10× cost cut on repeated system prompt + patient context). |
| **Multi-hospital, ≤10k users** | Migrate Railway → AWS (per the deployment doc's portability plan). ECS Fargate for Co-Pilot. Aurora MySQL replaces MariaDB. ElastiCache for FHIR cache + session state. Dedicated LLM gateway (internal service) fronts Anthropic + Bedrock with caching, retries, fallback. Self-hosted Langfuse replaces LangSmith. Vector store added if longitudinal queries enter scope. |
| **Multi-region SaaS, ≤100k users** | Active-active multi-region. Async tool dispatch via SQS. Bedrock provisioned throughput for predictable LLM cost and latency. Per-region Langfuse. Sensitive-encounter encryption with per-classification keys. |

The Co-Pilot service is **stateless** at the request level (state lives in Postgres + MariaDB). Horizontal scaling is therefore the dominant lever; vertical scaling is reserved for Postgres until read replicas absorb the load.

### 4.3 Failover and redundancy

| Component | Failure mode | Behavior |
|---|---|---|
| Co-Pilot service pod | Crash / restart | LangGraph state in Postgres survives; new pod picks up the conversation. Browser may see one failed turn. |
| Anthropic API | Outage | Bedrock fallback after 30s of 5xx. User sees a notice; chart UI behind it stays alive. |
| OpenEMR FHIR | Outage | Hard dependency. Agent and chart UI both go down. Acceptable — both depend on OpenEMR. |
| Postgres state store | Outage | Conversation state lost; running sessions show "session expired." New sessions blocked until Postgres is restored. |
| MariaDB / agent_audit | Outage | Hard fail-closed. Agent refuses to dispatch because audit cannot be written. |
| LangSmith | Outage | Service continues; traces queued or dropped. Not on the critical path. |

### 4.4 Monitoring and alerting

**LangSmith (engineer-facing):** every turn produces a trace with the full graph execution, tool calls, LLM calls, latencies, token counts, costs. Filterable by user, patient, role, decision, latency.

**`agent_audit` (compliance-facing):** every decision has an immutable hash-chained row. Daily verifier job checks the chain.

**Operational metrics** (Prometheus + Grafana, or Railway native):
- Response latency p50 / p95 / p99 per workflow
- Error rate (5xx from Co-Pilot)
- Verification refusal rate (target <2% in steady state)
- Tool failure rate per FHIR endpoint
- Cost per turn, per workflow, per role
- BAA-check status (binary, alarmed if false)

**Alerts:**
- Error rate >5% over 5 min
- p95 latency >15s over 10 min
- Verification refusal rate >5% over 10 min (signals a model regression or a verifier bug)
- BAA check fails at startup (page immediately)
- Audit hash-chain verifier fails (page immediately)
- LLM cost per turn >$0.50 over 10 min average (cost drift detection)

---

## 5. Trade-off Analysis

Each row scores a major decision on five dimensions: **complexity** (engineering work to build), **cost** (ongoing dollar cost), **familiarity** (how well-trodden the path is), **time-to-market** (week-1 viability), **maintainability** (how it ages). 1 = bad on that dimension, 5 = good.

| Decision | Complexity | Cost | Familiarity | Time-to-market | Maintainability | Choice + one-line rationale |
|---|---|---|---|---|---|---|
| **D1. Data API surface** | 5 | 5 | 5 | 5 | 5 | **SMART on FHIR.** Use OpenEMR's existing standards-compliant API. No custom data API to build, maintain, or fork. |
| **D2. Agent loop runtime** | 4 | 4 | 4 | 4 | 4 | **Python LangGraph in a separate service.** Mature ecosystem, native multi-turn state, parallel tool dispatch. PHP would force reimplementing what LangGraph gives free. |
| **D3. LLM provider** | 5 | 4 | 5 | 5 | 4 | **Anthropic single-vendor.** One BAA, one prompt-engineering surface, one eval suite. Bedrock failover documented. |
| **D4. Verification model** | 2 | 5 | 3 | 3 | 4 | **Bi-directional: input sentinels + output cite-or-refuse + data-layer canonicalization.** Most engineering of any decision; required for trust. |
| **D5. Authorization scoping** | 3 | 5 | 3 | 3 | 4 | **Per-role membership query + sensitive-encounter filter + break-glass.** OpenEMR has no per-patient ACL natively; this is the gap close. |
| **D6. Conversation state store** | 4 | 4 | 4 | 5 | 4 | **Postgres (Railway-managed).** LangGraph's officially supported checkpointer. Separate from MariaDB so PHI store stays clean. |
| **D7. Observability platform** | 5 | 3 | 4 | 5 | 3 | **LangSmith SaaS for MVP; Langfuse self-host before clinical go-live.** Fastest wire-up; defers the self-host operational cost until needed. |
| **D8. Free-text retrieval** | 5 | 5 | 4 | 5 | 3 | **Time-windowed FHIR query, no vector store.** Fits 24-hour scope. Adding embeddings is a week-2+ trigger when longitudinal queries enter scope. |
| **D9. Request shape** | 5 | 5 | 5 | 5 | 4 | **Synchronous request/response.** No queue at MVP. Fan-out is in-process parallel. Async path documented when p95 forces it. |
| **D10. UI integration** | 4 | 5 | 3 | 4 | 4 | **SMART EHR launch into iframe.** Standard pattern, no chart-UI code rewriting. Tighter inline panel is a future enhancement once UX testing argues for it. |

### 5.1 Per-decision narrative

**D1 — SMART on FHIR (vs custom data API).** A prior draft proposed custom REST endpoints inside OpenEMR to bypass FHIR's known performance issues (RIGHT JOINs against `patient_data`, N+1 in procedure reads). Interview review rejected this: latency is an engineering problem with engineering solutions (caching, `_include` batching, upstream PRs against the slowest paths), and going custom forfeits standards compliance, upstream community support, and portability to other FHIR-compliant EHRs. The free-text gap (nursing notes, cross-cover sign-out) is real but narrower than the prior draft suggested — `DocumentReference`, `Observation.note`, and `ClinicalImpression` cover most of it; remaining gaps are upstream contributions.

**D2 — LangGraph in Python.** Multi-turn conversation state and parallel tool dispatch are first-class in LangGraph. A pure-PHP loop would reimplement these. Splitting the loop into a separate service costs operational complexity (two deploys) but matches the boundary already implied by D1 (PHP doesn't host the data API; Python doesn't need to).

**D3 — Anthropic single-vendor.** A prior draft considered task-routing across Anthropic + OpenAI for best-of-breed per node. The drill walked this back: a second LLM vendor doubles the BAA surface (already 4 net-new BAAs in scope) and the prompt-engineering surface for marginal benefit. Sonnet 4.6 + Opus 4.7 + Haiku 4.5, statically routed by node, is sufficient. Bedrock is the documented failover path.

**D4 — Verification.** This is the load-bearing decision. Three layers:
- **Input sentinels** wrap PHI free-text fields (nursing notes, document filenames, etc.) before they enter the prompt. The system prompt instructs the model to treat sentinel content as untrusted data.
- **Output cite-or-refuse:** every clinical claim must carry a citation reference resolving to a FHIR resource fetched in this turn. The verifier parses the response, checks each claim's reference against the tool-output set, and blocks unsourced claims. Two regenerations with feedback, then explicit refusal.
- **Data-layer canonicalization:** medication lifecycle status, condition status, demographic inconsistencies are computed in the tool wrappers before the LLM sees them. The model can't misclassify what the data layer already canonicalized.

The complexity score is low (2/5) because all three layers must work together. The maintainability score is decent (4/5) because the rules are explicit and unit-testable.

**D5 — Authorization.** OpenEMR's stock authorization is role-based; once authenticated, any clinician can see any patient. The agent cannot inherit that gap because an LLM with broad role grants and natural-language input is a faster way to enumerate other patients than the chart UI is. Per-role membership queries (care-team for hospitalist, active consult for consultant, unit panel for pharmacist) gate every FHIR call. Sensitive-encounter ACL filtering happens at the retriever, before responses enter the LLM context. Break-glass uses OpenEMR's existing `BreakglassChecker` plus an agent-side justification prompt.

**D6 — Postgres (vs storing LangGraph state in MariaDB).** LangGraph's reference checkpointer is Postgres. MariaDB would require a custom checkpointer, more code to maintain, and entangling agent runtime state with PHI storage. The two stores have different lifecycles (PHI is HIPAA-retention 6 years; conversation state is session-scoped) — separating them is the cleaner data model.

**D7 — LangSmith MVP / Langfuse production.** LangSmith is the fastest path to "I can see what the agent did" (native LangGraph integration, hosted, zero ops). It requires a BAA before real PHI. The synthetic-data MVP is fine without that BAA; pre-clinical-go-live is the trigger to swap to self-hosted Langfuse on the same cloud account, dropping the third-party dependency.

**D8 — Time-windowed retrieval.** The user workflows scope retrieval to "last 24 hours" (W-2), "last 12 hours" (W-3), or "since last consult" (W-9). All fit comfortably inside Anthropic's context window with sentinel-wrapped notes. A vector store buys nothing for this scope. Trigger for embeddings: longitudinal queries enter scope ("show me every note that mentioned chest pain this admission") — week 2+.

**D9 — Synchronous request/response.** Fan-out is parallel inside one process. No queue between browser and Co-Pilot. No queue between Co-Pilot and FHIR. This works at MVP scale; at higher scales, the documented trigger is p95 latency >30s for 10 minutes consistent — at that point tool calls move to a worker pool with WebSocket streaming.

**D10 — Iframe via SMART EHR launch.** Standard pattern for EHR-embedded apps. Avoids modifying OpenEMR's chart UI templates beyond a button injection. A tighter inline panel (rendering inside the chart's existing tab system) is a future enhancement gated on UX testing.

---

## 6. What I'd revisit as the system grows

The SKILL framework asks: identify what to revisit as the system grows. The honest list:

1. **D1 — FHIR latency.** OpenEMR's stock FHIR layer has identified slow paths. By the 1k-user tier, upstream PRs or a forked OpenEMR with the patches applied are mandatory. Revisit when p95 latency exceeds 15s consistently.
2. **D3 — Single-vendor LLM.** When Anthropic outages start showing up in incidents, or when an evaluation finds a node where another model materially wins, expand to Bedrock or a parallel vendor. Trigger: ≥2 user-visible outages in a quarter, or eval-win delta ≥10% on a node.
3. **D7 — Observability.** Langfuse self-host is the trigger for any real PHI. Don't wait for the migration to be perfect — pre-stage during the Railway → AWS move.
4. **D8 — Vector store.** Trigger is the longitudinal query: a workflow that explicitly needs cross-admission or cross-encounter prose retrieval. Today, none of W-1 through W-11 require this.
5. **D5 — Authorization granularity.** Today the per-role membership query is correct but coarse. The first time a tenant asks for time-windowed grants ("this consultant has access only during their on-call shift"), the model needs to extend.
6. **D9 — Async path.** The documented trigger is p95 latency. A second trigger is streaming UX — clinicians may want token-by-token output. That requires verification to run mid-stream, which is a non-trivial rework.

---

## 7. Open questions

- **Free-text gaps in FHIR US Core.** A small list of OpenEMR free-text fields don't have a clean FHIR US Core mapping today. Two paths under consideration: contribute upstream FHIR profile extensions, or accept degraded coverage with a clear roadmap. Need to enumerate the gaps explicitly before final submission.
- **N+1 in OpenEMR's FHIR services.** Audit identified specific bottlenecks. Decision pending: contribute upstream PRs (slow but right) versus run a forked OpenEMR with the patches applied (fast but a maintenance burden).
- **Iframe vs separate window for the SMART app.** Iframe gives tighter chart integration; separate window gives more screen real estate. User testing required.
- **Per-role launch ergonomics.** Hospitalists open the agent from a patient chart; pharmacists open from a unit dashboard; consultants open from a consult-list page. Whether the UI surface is one button or three remains to be designed.
- **Conversation retention default.** 24 hours of LangGraph state is a guess. Actual hospitalist usage may want 12-hour shift-bounded sessions or persistent multi-day conversations. Eval and user feedback will tune this.

---

## 8. Future scope

The agent's design is intentionally narrow at MVP. The following are forward-looking expansions, each gated on a specific trigger:

| Feature | Trigger to build |
|---|---|
| Discharge summary drafting | Eval evidence that hospitalists trust the agent's per-patient brief enough to use it as a draft input. The draft remains read-by-the-agent, written-by-the-clinician. |
| Cross-service handoff brief at end of shift | A second user role (nocturnist or weekend hospitalist) is onboarded as a primary user, not secondary. |
| Voice input | Clinical pilot feedback shows hands-busy contexts where typing is the constraint (W-3 pager workflow is the first candidate). |
| Token-by-token streaming responses | Verification can run mid-stream — a non-trivial rework where each token's claim status is checked as it arrives. |
| Patient-facing portal mode | Whole new threat model (no clinician in the loop). Out of scope for week 1; non-trivial for week 2+. |
| Cross-encounter longitudinal queries | A workflow explicitly needing prose retrieval beyond the 24-hour window. Triggers vector-store addition (D8). |
| Order-suggestion mode (still no auto-order) | Strong eval evidence that the synthesis is fact-faithful at 99%+ and a clinical workflow asks for it. The agent still does not place orders; it surfaces candidate orders for the clinician to review. |
| Multi-tenant per-hospital configuration | Second hospital deployment. MVP is single-tenant. |
