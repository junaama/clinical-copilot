## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Deploy the Week 2 agent to Railway with all new capabilities working, add per-encounter cost/latency observability, and verify Week 1 + Week 2 behavior works independently on the deployed instance.

**Deployment:**
- Add new pip deps to `pyproject.toml`: `pymupdf`, `pgvector`, `cohere`
- Add `COHERE_API_KEY` to Railway env vars
- Run DB migration on deploy (pgvector extension + guideline_chunks table + document_extractions table)
- Run guideline indexer on first deploy (idempotent)
- Verify OpenEMR document API is accessible from the agent service

**Observability (per-encounter trace):**
- Tool sequence logged (which tools called, in what order)
- Latency by step: supervisor decision, VLM extraction (per page), retrieval query, rerank, synthesis
- Token usage per LLM call
- Cost estimate per encounter (model-specific rates: Sonnet vision, Sonnet text, Cohere embed, Cohere rerank)
- Retrieval hits (which chunks retrieved, scores)
- Extraction confidence (per-field)
- No raw PHI in traces (patient referenced by ID, no document text in logs)

**Cost/latency report:**
- Document actual dev spend during Week 2
- Project per-encounter production cost
- p50/p95 latency for document ingestion flow and evidence retrieval flow
- Identify bottleneck (likely VLM extraction for multi-page PDFs)

## Acceptance criteria

- [ ] Agent deploys to Railway with Week 2 capabilities active
- [ ] `COHERE_API_KEY` configured in Railway
- [ ] DB migration runs successfully on deploy
- [ ] Guideline corpus indexed on deployed instance
- [ ] File upload → extraction works end-to-end on deployed app
- [ ] Evidence retrieval works end-to-end on deployed app
- [ ] Week 1 workflows (triage, brief) still work on deployed app
- [ ] Per-encounter traces include: tool sequence, latency by step, token usage, cost estimate
- [ ] Traces contain no raw PHI
- [ ] Cost/latency report written with actual numbers from deployed runs

## Blocked by

- `issues/009-supervisor-workers-classifier.md` (full pipeline must work)
- `issues/010-eval-gate-50-cases-pre-push-hook.md` (eval gate must pass before deploy)

## User stories addressed

- User story 18 (per-encounter cost and latency tracking)
- User story 20 (test Week 1 and Week 2 independently)
