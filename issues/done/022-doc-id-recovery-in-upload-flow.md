## Parent PRD

`issues/prd-doc-id-recovery-and-extraction-cache.md`

## What to build

Replace the synthetic `openemr-upload-<sha-hex>` id with the real OpenEMR
DocumentReference id at upload time. When the OpenEMR Standard API returns
the bool-given 500 (a successful save with a broken response serializer),
the document client recovers the real id by listing the patient's
documents in the same category and matching by filename + recency. If
recovery fails, the helper surfaces an unambiguous error instead of
inventing an id; `/upload` maps that error to a clear UI message so the
clinician knows to re-attach.

See parent PRD: "Recovery match key (fix A)" and "Recovery failure handling
(fix A)" for the match policy and failure contract.

## Acceptance criteria

- [ ] On a normal 201/200 upload response, the helper still returns the id
      from the response body — no extra list call, no behavioral change to
      the happy path.
- [ ] On a 500 response whose body fingerprint matches `bool given` +
      `getResponseForPayload`, the helper performs a list call against the
      patient's documents in the uploaded category and returns the most
      recent entry whose filename matches exactly and whose timestamp is
      within ~60 seconds of the upload attempt.
- [ ] When the recovery list call returns transport error, non-2xx, or no
      match, the helper returns a clean error tuple with a stable code
      (e.g. `upload_landed_id_lost`). No synthetic id is returned.
- [ ] No call site in the agent ever sees a string of the form
      `openemr-upload-<hex>` after this slice lands; existing references
      in production state are tolerated but not produced.
- [ ] `/upload` translates the recovery-failure error into an
      HTTP-level response shape the UI already understands; the user gets
      a "upload landed but the document id couldn't be confirmed; please
      re-attach" message instead of a confusing 404 a few turns later.
- [ ] `tests/test_document_client.py` covers four cases under mocked
      `httpx`: bool-given 500 + clean list match, bool-given 500 + list
      5xx, bool-given 500 + empty/non-matching list, happy path 201.
- [ ] `tests/test_graph_e2e_live.py::test_e2e_upload_then_extract_lab_pdf`
      asserts the doc id flowing through state has no `openemr-upload-`
      prefix.
- [ ] Manual verification against the deployed agent: upload
      `p04-kowalski-cmp.pdf` for a real patient and confirm the next chat
      turn cites a real `DocumentReference/<id>` and walks through what's
      notable, with no apology turn.

## Blocked by

None — can start immediately.

## User stories addressed

Reference by number from the parent PRD:

- User story 1
- User story 2
- User story 3
- User story 6
- User story 7
- User story 10
- User story 11
- User story 14
