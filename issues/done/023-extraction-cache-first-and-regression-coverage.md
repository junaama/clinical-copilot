## Parent PRD

`issues/prd-doc-id-recovery-and-extraction-cache.md`

## What to build

Make `extract_document` cache-first so that post-upload chat turns serve
cached extractions instead of re-downloading the document and re-running
the VLM. The store gains a content-hash lookup as a defense-in-depth
fallback; `/upload` writes both keys when it persists an upload-time
extraction. The tool checks `(patient_id, document_id)` first, falls back
to `(patient_id, filename, sha256(bytes))`, and only runs the full
download + VLM pipeline on a miss. Emits a `cache_hit` signal in logs and
the Langfuse span so the cache effect is observable.

This slice also lands the regression coverage that the PRD calls for —
one new graph-integration test case pinning the cache-first behavior at
the graph level, plus a tightening of the live e2e test to assert the
second extraction is cache-served.

See parent PRD: "Cache keys (fix B)", "Cache freshness (fix B)", and
"Observability".

## Acceptance criteria

- [ ] `document_extractions` storage gains an indexed `content_sha256`
      column; rows written by the upload-time extraction populate it
      alongside `document_id` and `filename`. Lookups by id continue to
      work unchanged.
- [ ] The store exposes a hash-keyed lookup scoped to `patient_id` that
      returns the same row a `document_id` lookup would return, when the
      row was written with both keys.
- [ ] `extract_document` consults the store before invoking the document
      client's `download` or the VLM model: primary key
      `(patient_id, document_id)`, fallback `(patient_id, filename,
      sha256(bytes))`. On hit, the tool returns the cached payload and
      issues no Anthropic vision call.
- [ ] On a miss, the tool runs the existing pipeline and writes the
      result back to the store under both keys before returning.
- [ ] Every `extract_document` call emits a structured log line and a
      Langfuse span attribute named `cache_hit` (boolean), plus the
      resolved cache key. No PHI in the log payload.
- [ ] Unit test for the store: a row written via the id path is
      retrievable via the hash path within the same patient scope; the
      same hash under a different `patient_id` does not match.
- [ ] Unit test for the tool: with the store pre-populated for
      `(patient_id, document_id)`, the tool returns the cached payload
      and the document-client `download` mock and the VLM mock are both
      called zero times. A second test asserts the same for the fallback
      `(patient_id, filename, sha256)` path.
- [ ] New regression case in `tests/test_graph_integration.py` builds
      the full graph, pre-populates the extraction store, posts a
      `[system] Document uploaded:` sentinel + the synthetic chat turn,
      asserts the worker reaches a terminal AIMessage citing the real
      `DocumentReference/<id>`, and asserts the VLM stub was called zero
      times. Stays sub-second; runs in the pre-push gate.
- [ ] `tests/test_graph_e2e_live.py::test_e2e_upload_then_extract_lab_pdf`
      issues a second chat turn about the same document and asserts the
      second extraction is cache-served (zero new VLM calls observable
      via store row count or `cache_hit: true` in the trace).
- [ ] Manual verification against the deployed agent: a follow-up
      question about an already-uploaded document returns within the
      normal chat-turn budget and shows `cache_hit: true` in the
      Langfuse span for the worker's `extract_document` call.

## Blocked by

- Blocked by `issues/022-doc-id-recovery-in-upload-flow.md` (so the
  primary cache key `(patient_id, document_id)` resolves against real
  OpenEMR ids end-to-end; the fallback hash key handles legacy synthetic
  ids that may still be in flight).

## User stories addressed

Reference by number from the parent PRD:

- User story 4
- User story 5
- User story 8
- User story 9
- User story 12
- User story 13
