## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Expose drawable document source boxes as part of the upload response so the UI
can render a source-grounding overlay without guessing at backend state. This
is the first vertical slice of the submission-pass source grounding work:
upload a document, run extraction, compute bboxes, return only drawable bbox
records, and prove the response contract with tests.

## Acceptance criteria

- [ ] `/upload` responses include a `bboxes` array for successful lab PDF and
      intake form uploads.
- [ ] Each returned bbox record includes field path, extracted value, matched
      text, non-null normalized geometry, page number, and match confidence.
- [ ] Bbox records without drawable geometry are filtered at the response
      boundary and are not returned to the frontend.
- [ ] Internal extraction persistence/cache behavior remains compatible with
      the existing bbox matcher and store behavior.
- [ ] Frontend API types represent upload bboxes as drawable-only records with
      non-null geometry.
- [ ] Backend tests assert both positive bbox response behavior and filtering of
      non-drawable bbox records.
- [ ] Existing W2 upload/extraction tests continue to pass.

## Blocked by

None - can start immediately.

## User stories addressed

- User story 22
- User story 23
- User story 24
