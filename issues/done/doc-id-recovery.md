# PRD - Doc-ID Recovery + Extraction Cache

## Problem Statement

A clinician uploads a lab PDF for a patient. The OpenEMR Standard API saves
the bytes successfully but throws a 500 when serializing the response because
the upstream route returns a boolean to a response helper that expects an
array. The agent upload client currently masks that failure with a synthesized
id of the form `openemr-upload-<sha-hex>` so the upload can report success and
the upload-time VLM extraction can run against the bytes in-process.

That works only for the upload moment. It breaks the next chat turn:

- The synthetic id is not a real OpenEMR `DocumentReference` id.
- When the supervisor dispatches the document worker for the post-upload turn
  and the worker calls `extract_document` by id, OpenEMR returns `http_404`.
- The user can see extracted results in the panel while the chat says it could
  not read the document.

The result is a contradictory product experience: the panel says the document
was decoded, while the chat says the document is unavailable. A second cost is
that, even when the id is real, the worker can re-run VLM extraction on a
document that was already extracted during upload, creating duplicate latency
and duplicate model spend.

## Solution

Implement two coupled fixes.

First, recover the real OpenEMR document id at upload time. When the
bool-given 500 fires, the document client should list the patient's documents
in the uploaded category, match the just-uploaded file by exact filename and
recency, and return the real id instead of a synthesized one. If recovery
fails, the upload should surface an explicit recoverable error and no synthetic
id should enter state.

Second, make extraction cache-first. The document extraction tool should check
the extraction store before downloading the document or calling the VLM. If a
recent extraction exists for the same patient and document id, it should return
that cached result. As a defense-in-depth fallback, the store should also
support lookup by patient, filename, and content hash for legacy or re-upload
cases. Post-upload chat turns should use the extraction produced by `/upload`;
the VLM should run only once per uploaded document.

The user-visible outcome: after upload, the next chat turn cites the real
`DocumentReference/<id>` and walks through what is notable. The extraction
panel and chat agree. Duplicate VLM calls are avoided.

## User Stories

1. As a clinician prepping for a visit, I want the post-upload chat turn to
   walk me through what is notable in the document I just uploaded, so that I
   can use the upload flow as a one-step review action.
2. As a clinician, I do not want the agent to apologize about a missing
   document a moment after I watched it upload successfully, so that I trust
   what the agent says.
3. As a clinician looking at the extraction panel and the chat side-by-side, I
   want them to agree on whether the document was read, so that I do not have
   to reconcile two contradictory views of the same upload.
4. As a clinician, I want follow-up questions about the same uploaded document
   to answer without a noticeable delay, so that the chat keeps up with chart
   review.
5. As an operator paying per VLM call, I do not want a second vision charge
   every time the chat asks about a document the upload flow already extracted,
   so that cost scales with documents rather than turns.
6. As an engineer, I want masking of the OpenEMR bool-given 500 to be
   self-contained inside the document client, so that the rest of the agent
   never sees a synthetic id.
7. As an engineer debugging extraction, I want every persisted extraction row
   to point at a real OpenEMR id, so that I can correlate the row with what the
   EHR shows for that patient.
8. As an engineer reading traces, I want the worker tool call and persisted
   extraction row to share the same document id, so that one trace can be
   followed end-to-end.
9. As an engineer responsible for cost, I want logs and traces to show whether
   an extraction was served from cache or recomputed, so that cache behavior is
   observable.
10. As a clinician, when the upload genuinely fails or the real id cannot be
   recovered, I want a clear retry message immediately, so that I know to
   re-attach the file.
11. As an engineer rolling this out, I do not want a regression in the happy
   path where OpenEMR returns a normal response with a real id, so that the fix
   only changes the broken-upstream branch.
12. As an engineer, I want graph-level regression coverage proving post-upload
   chat does not re-dispatch the VLM when an extraction already exists, so that
   future refactors cannot bring back duplicate calls.
13. As an engineer running live e2e checks, I want upload-then-chat tests to
   assert the second extraction is served from cache, so that the behavior is
   verified end-to-end.
14. As a clinician with two PDFs of the same name uploaded back-to-back, I want
   each upload to map to its own document, so that chat about the second upload
   does not read the first extraction.

## Implementation Decisions

- Keep the normal upload response path unchanged. When OpenEMR returns a valid
  id, use that id and do not perform a recovery list call.
- On the known bool-given 500 response, perform a recovery list call scoped to
  the same patient and document category.
- Match the recovered document by exact filename and a short recency window
  around the attempted upload time. Pick the most recent matching entry.
- If recovery fails because the list call errors or no matching entry exists,
  return a stable recoverable error. Do not synthesize an id.
- Remove production of new `openemr-upload-<sha>` identifiers. Existing legacy
  state may be tolerated, but new uploads should not create those ids.
- Store upload-time extractions under the real document id whenever available.
- Add or use a patient-scoped content-hash lookup in the extraction store so
  cached rows can be found by `(patient_id, filename, content_sha256)` as a
  fallback.
- Make the `extract_document` tool check the extraction store before document
  download or VLM invocation.
- On a cache hit, return the same external extraction payload shape as a fresh
  extraction and emit an internal cache-hit telemetry signal.
- On a cache miss, preserve the existing download, VLM extraction, persistence,
  and return behavior.
- Treat OpenEMR documents as immutable for this feature. Cached extractions do
  not need TTL-based expiration in this PRD.
- Scope every cache lookup by patient id to prevent cross-patient leakage.
- Log cache hits, cache misses, recovery success, and recovery failure with
  structured non-PHI fields.
- Keep the public upload API shape compatible with the existing UI error
  handling, while making the error text clear enough for retry.

## Testing Decisions

- Good tests should cover external behavior: return tuples, API responses,
  persisted rows, graph outputs, and model/client call counts. Avoid tests that
  lock private helper names.
- Add document-client unit tests with mocked HTTP responses for normal success,
  bool-given 500 with successful recovery, recovery list failure, and recovery
  no-match.
- Add upload handler tests proving recovery failure produces a clear user-safe
  error and does not inject a synthetic id.
- Add extraction-store tests proving a row written with document id, filename,
  and content hash can be retrieved by id and by hash, and that the same hash
  under a different patient does not match.
- Add extraction-tool tests proving a pre-populated store row avoids document
  download and VLM calls.
- Add extraction-tool tests for both primary id cache hits and fallback hash
  cache hits.
- Add graph integration coverage where a post-upload sentinel plus synthetic
  chat turn reaches a terminal assistant answer that cites the real
  `DocumentReference/<id>` and does not call the VLM.
- Tighten live upload e2e coverage so document ids flowing through state do not
  have the `openemr-upload-` prefix.
- Add or tighten live e2e coverage proving a second chat turn about the same
  uploaded document is served from cache.
- Prior art includes the existing document client tests, extraction store
  tests, graph integration tests, and live upload e2e test.

## Out of Scope

- Fixing the upstream OpenEMR response serializer bug directly.
- Building a historical backfill for rows already stored under synthetic ids.
- Adding a document deletion or cache purge flow.
- Replacing the VLM extraction model.
- Changing document categories beyond the existing lab PDF and intake form
  mapping.
- Reworking the frontend extraction panel.
- Re-indexing guideline retrieval or changing RAG behavior.

## Further Notes

- The related implementation slices are listed in the done issues for doc-id
  recovery and extraction cache-first regression coverage.
- The recovery flow is a compatibility shim for the deployed OpenEMR image. If
  the upstream API later returns a normal id reliably, the recovery branch
  should become rarely used but can remain harmless.
- The cache-first behavior is valuable even after the OpenEMR response bug is
  fixed because it avoids duplicate VLM calls for post-upload chat turns.
