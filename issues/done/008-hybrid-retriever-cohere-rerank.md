## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Create the hybrid retrieval pipeline that searches the guideline corpus using both sparse (tsvector) and dense (pgvector) search, combines results via Reciprocal Rank Fusion, reranks with Cohere, and returns cited evidence chunks.

**Module:** `agent/src/copilot/retrieval/retriever.py`

**Pipeline:**
1. Accept: query string + optional domain_filter (guideline name)
2. Embed query via Cohere `embed-english-v3.0` (input_type="search_query")
3. Single Postgres query: `ts_rank(tsv, plainto_tsquery(:q))` for sparse + `1 - (embedding <=> :qvec)` for dense, combined via RRF, LIMIT 20
4. Pass 20 candidates to Cohere `rerank-english-v3.0` with the original query
5. Return top-5 chunks as `EvidenceChunk` objects with relevance scores and `SourceCitation` metadata
6. Fallback: if Cohere rerank fails, return top-5 by RRF score

**Also create:** `retrieve_evidence` tool in `tools/extraction.py` (or a new `tools/retrieval.py`) that the evidence-retriever worker calls.

## Acceptance criteria

- [ ] `retrieve(query, top_k=5, domain_filter=None) → list[EvidenceChunk]` function
- [ ] Hybrid query combines sparse + dense via RRF in a single Postgres roundtrip
- [ ] Cohere rerank called with query + candidate passages
- [ ] Top-5 chunks returned with relevance_score and full SourceCitation (guideline_name, section, page, chunk_id)
- [ ] Fallback to RRF-only when Cohere is unavailable (tested)
- [ ] `retrieve_evidence` tool wraps the retriever with CareTeam gate
- [ ] Integration test against seeded pgvector: known query returns expected guideline chunk in top-5
- [ ] Unit test (mocked Cohere): rerank reorders candidates correctly

## Blocked by

- `issues/007-pgvector-migration-guideline-indexer.md` (corpus must be indexed first)

## User stories addressed

- User story 7 (ask about guidelines, get cited answer)
- User story 8 (cite guideline name, section, page)
- User story 9 (separate chart facts from guideline evidence)
- User story 10 (refuse when no relevant evidence found)
