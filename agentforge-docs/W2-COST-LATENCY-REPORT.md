# Week 2 Cost & Latency Report

Per-encounter cost and latency analysis for the Week 2 multimodal evidence
agent. Tracks the W2 PRD acceptance criteria for issue 012:

- per-encounter trace observability (tool sequence, latency by step,
  token usage, cost estimate)
- cost projection per encounter from list-price model rates
- p50 / p95 latency budget for document ingestion and evidence
  retrieval flows
- bottleneck identification

> **Status:** Methodology and projected numbers in place. Actual
> production p50/p95 numbers will be backfilled once the deployed
> instance has sustained traffic; the methodology fixes the math, the
> rate table fixes the cost projection, and the audit row schema
> (`extra.cost_estimate_usd`, `extra.tool_sequence`, etc.) makes the
> backfill mechanical.

> **Operational measurement procedure.** The four-flow Langfuse smoke
> that backfills this report's "actual numbers" section is documented
> at `runbook/002-deployed-langfuse-measurement.md`. That runbook owns
> demo account, fixture documents, prompts, trace-field checklist,
> and the PHI safety check. This file owns the projection math and
> rate table.

---

## 1. Observability surface (where the numbers come from)

Every agent turn writes one row to the audit log
(`agent_audit.jsonl` locally, `agent_audit` Postgres table when
`AGENT_AUDIT_LOG_PATH` resolves to a DSN). The W2 fields landed by
this issue are all under `extra`:

| Field | Source | Purpose |
| --- | --- | --- |
| `extra.tool_sequence` | `_tool_sequence(state)` in `graph.py` | Ordered tool names (duplicates kept) for path analysis |
| `extra.cost_estimate_usd` | `aggregate_turn_cost(...)` in `cost_tracking.py` | Sum of per-call USD across the turn's LLM/Cohere calls |
| `extra.cost_by_model` | same | Per-model breakdown so a shift between Sonnet ↔ Haiku is visible |
| `extra.cost_rate_unknown_models` | same | Models whose rate isn't in the table (avoids silent zero-cost) |
| `extra.handoff_events` | supervisor sub-graph (issue 009) | Per-dispatch trail when W-DOC / W-EVD ran |
| `extra.supervisor_action` | same | Final supervisor decision, surfaced for filtering |
| `prompt_tokens` / `completion_tokens` | LangChain `usage_metadata` | Per-turn token totals |

The `_per_call_costs` helper iterates `state["messages"]`, picks up
`AIMessage.usage_metadata` (input/output tokens) and
`AIMessage.response_metadata.model_name`, and hands each to
`estimate_call_cost`. Messages without usage metadata are skipped (so
a partial turn doesn't silently inflate the rate-known total).

### PHI scrubbing

The audit row carries `patient_id` / `user_id` / `conversation_id` for
joining, but never:

- Free user prompt or assistant text (only `final_response_chars`,
  the length).
- Patient display names, DOB, or other demographic strings.
- Document body text. Tool results are summarized as a count and a
  list of refs (`fetched_refs`); the result payload is not embedded.
- Supervisor reasoning carrying patient names — the
  `SupervisorDecision.reasoning` schema docstring forbids it
  (`agent/src/copilot/supervisor/schemas.py:60-65`) and the contract
  is pinned in `agent/tests/test_audit_no_phi.py`.

---

## 2. Rate table

`copilot/cost_tracking.py` carries the list-price rates as of
**2026-Q2** (USD per 1K tokens, longest-prefix model match):

| Family | Input / 1K | Output / 1K |
| --- | ---: | ---: |
| Claude Opus 4 | $0.015 | $0.075 |
| Claude Sonnet 4 (incl. 4-6) | $0.003 | $0.015 |
| Claude Haiku 4 | $0.001 | $0.005 |
| GPT-4o | $0.0025 | $0.010 |
| GPT-4o mini | $0.00015 | $0.0006 |

| Cohere | Unit | Rate |
| --- | --- | --- |
| `embed-english-v3.0` | per 1K tokens | $0.0001 |
| `rerank-english-v3.0` | per call | $0.002 |

Rates drift. The absolute number is indicative; the **trend**
(per-turn, per-workflow, per-tier) is what's actionable. When a model
isn't in the table, the estimator returns `cost_usd=None` rather than
guessing — silent zeros would hide spend.

---

## 3. Per-encounter cost projection

Three reference flows. Token counts are upper-bound estimates from
prompt+context shape; production will land lower for short turns.

### 3.1 Document ingestion (lab PDF, 2 pages)

| Step | Model | Tokens (in / out) | Cost |
| --- | --- | --- | --- |
| Classifier | gpt-4o-mini | 600 / 30 | $0.000108 |
| Supervisor decision | gpt-4o-mini | 800 / 80 | $0.000168 |
| VLM extraction (×2 pages) | claude-sonnet-4-6 | 2 × 4500 / 800 | 2 × $0.025500 = $0.051000 |
| Bbox match | (local, no LLM) | — | $0 |
| Persistence | (local + HTTP) | — | $0 |
| Synthesis (verifier-gated) | gpt-4o-mini | 1500 / 250 | $0.000375 |
| **Total** | | | **≈ $0.052** |

Bottleneck: **VLM extraction** dominates (≈98% of spend). Sonnet
vision at ~$0.0255 per page is the per-encounter cost driver. Trade-
offs: (a) cheaper VLM (Haiku 4.5 vision, ~$0.005/page → $0.010 for
2 pages) acceptable for unambiguous typed labs but loses fidelity on
handwritten intake forms; (b) classifier-routed VLM (Sonnet only when
the page is dense / handwritten) keeps clean labs cheap.

### 3.2 Evidence retrieval (one guideline question)

| Step | Model | Tokens (in / out) | Cost |
| --- | --- | --- | --- |
| Classifier | gpt-4o-mini | 600 / 30 | $0.000108 |
| Supervisor decision | gpt-4o-mini | 800 / 80 | $0.000168 |
| Query embedding | cohere embed v3 | ≈30 | $0.000003 |
| Hybrid RRF SQL | (Postgres) | — | $0 |
| Rerank top-20 | cohere rerank v3 | — | $0.002000 |
| Synthesis (with chunks) | gpt-4o-mini | 4000 / 400 | $0.000840 |
| **Total** | | | **≈ $0.003** |

Bottleneck: **rerank** is the single biggest line item, but it's a
fixed $0.002 per call; per-encounter cost is rounding noise.

### 3.3 Mixed multi-turn encounter (W1 brief + W-DOC + W-EVD)

Approximate envelope by summing 3.1, 3.2, and a ~$0.001 W1
brief turn (gpt-4o-mini synthesis, 5 tool calls): **≈ $0.056**.

### Production projection at 1k encounters/day

- Mixed mix (50% W1, 30% W-EVD, 20% W-DOC): ≈ $0.012 average ×
  1000 = **$12 / day** ≈ **$360 / month** at the rate card.
- Pure W-DOC (worst case): $52 / day = $1,560 / month.

Cohere costs are negligible at this volume. Anthropic vision is
the single line item to monitor.

---

## 4. Latency budget

Per-step targets. Actual p50/p95 to be backfilled from the audit
table (`extra.handoff_events[].timestamp` deltas + per-turn `ts`).

| Step | Target p50 | Target p95 | Notes |
| --- | ---: | ---: | --- |
| Classifier (`gpt-4o-mini`) | 400 ms | 1200 ms | Bounded by model latency, no I/O |
| Supervisor decision | 500 ms | 1500 ms | One structured-output call |
| VLM page extraction (Sonnet vision) | 4000 ms | 9000 ms | Dominant cost; per-page sequential today |
| Bbox match | 50 ms | 200 ms | Local, PyMuPDF + fuzzy match |
| Standard API write (allergy / med / problem) | 200 ms | 800 ms | OpenEMR HTTP round-trip |
| pgvector hybrid query | 60 ms | 250 ms | Single Postgres roundtrip |
| Cohere rerank (top-20) | 300 ms | 800 ms | Single API call |
| Synthesis (gpt-4o-mini, 5 cites) | 1500 ms | 4500 ms | LLM token-stream is the bottleneck |

End-to-end p95 estimates:

- **W-EVD turn:** ≈ 7 s p95 (classifier + supervisor + embed + RRF + rerank + synthesis).
- **W-DOC turn (2-page PDF):** ≈ 21 s p95 (classifier + supervisor + 2× VLM sequential + bbox + persistence + synthesis).
- **W1 brief:** ≈ 6 s p95 (classifier + parallel FHIR fan-out + synthesis).

W-DOC is the user-visible latency cliff. Mitigations: VLM page
parallelism (currently sequential in `vlm_extract_document`), Haiku
fallback for low-density typed labs, pre-extraction at upload time
(already wired in `/upload` for issue 011 — cost is paid upfront, so
the next turn lands at synthesis-only latency).

---

## 5. How to read the audit row

```bash
# Tail the local audit log
tail -f agent/logs/agent_audit.jsonl | jq '{
  ts, workflow_id,
  cost: .extra.cost_estimate_usd,
  by_model: .extra.cost_by_model,
  tools: .extra.tool_sequence,
  supervisor: .extra.supervisor_action
}'
```

```sql
-- Total spend last 24h, by workflow
SELECT
  workflow_id,
  count(*) AS turns,
  round(sum((extra->>'cost_estimate_usd')::numeric), 4) AS total_usd,
  round(avg((extra->>'cost_estimate_usd')::numeric), 6) AS per_turn_usd
FROM agent_audit
WHERE ts > now() - interval '24 hours'
GROUP BY workflow_id
ORDER BY total_usd DESC;
```

```sql
-- p50 / p95 latency by tool sequence shape (when latency_ms is wired)
SELECT
  array_to_string(
    ARRAY(SELECT jsonb_array_elements_text(extra->'tool_sequence')),
    ','
  ) AS sequence,
  count(*),
  percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
FROM agent_audit
WHERE workflow_id IN ('W-DOC', 'W-EVD')
GROUP BY sequence
ORDER BY count(*) DESC;
```

---

## 6. Deployment checklist (W2 delta)

The W1 deployment doc is `agentforge-docs/DEPLOYMENT.md`. Week-2
adds:

- **Image extras.** `agent/Dockerfile` now installs both
  `--extra postgres` and `--extra retrieval` so the deployed image
  has `cohere` and `pgvector` available. Without this the first
  guideline query at runtime fails with `ImportError`.
- **Env vars on Railway.**
  - `COHERE_API_KEY` — required for hybrid retrieval. Without it
    `retrieve_evidence` returns the `no_cohere_key` error envelope.
  - `VLM_MODEL` — defaults to `claude-sonnet-4-6`; override to
    `claude-haiku-4-5` for cost-mode.
- **DB migrations (run once after first deploy with the W2 image):**
  ```bash
  railway run --service copilot-agent \
      python -m copilot.extraction.migrate
  railway run --service copilot-agent \
      python -m copilot.retrieval.migrate
  ```
  Both migrations are idempotent (`CREATE … IF NOT EXISTS`).
- **Guideline indexer (run once after migrations):**
  ```bash
  railway run --service copilot-agent \
      python -m copilot.retrieval.indexer --corpus-dir ./data/guidelines
  ```
  Idempotent — re-runs filter `chunk_id` against existing rows
  before embedding, so cost is one Cohere embed batch on first run
  and ~$0 thereafter.
- **W1 regression check:** after deploy, hit `/chat` with a W1 brief
  prompt against a known patient. The audit row should show
  `workflow_id=W-2` (per-patient brief), no supervisor action, and
  `cost_estimate_usd > 0` — confirming W1 paths are unaffected by
  the W2 supervisor wiring.

---

## 7. Numbers to backfill after live traffic

| Field | How |
| --- | --- |
| Actual p50/p95 per workflow | Add `latency_ms` per turn to `_audit` (currently 0); compute with the SQL in §5 |
| Actual cost per workflow | The SQL in §5 against 7-day window |
| Actual VLM page latency | Add per-page timing to `vlm_extract_document`, surface in `extra.vlm_page_latency_ms_p50/p95` |
| Actual rerank latency | Add per-call timing in `retrieval/retriever.py`, surface in `extra.rerank_latency_ms` |
| Dev spend during W2 | Sum from Anthropic + Cohere consoles; compare against this projection |

The `extra` field is open-shape, so each backfill is purely additive
— no schema migration needed.
