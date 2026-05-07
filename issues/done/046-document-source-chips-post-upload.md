## Parent PRD

`issues/prd.md`

## What to build

Render document source chips in post-upload chat answers when uploaded-document
extraction is the supporting evidence. Document-sourced claims should remain
tied to the uploaded file and should not appear as unsupported chart truth.

## Acceptance criteria

- [ ] Post-upload chat responses can include document citation metadata when
      an uploaded document supports the answer.
- [ ] Document citation chips render in the chat UI using a human-readable
      document label.
- [ ] Document-sourced facts are framed as document evidence rather than
      automatically persisted chart truth.
- [ ] Answers without document source metadata narrow or state the limitation
      instead of implying a citation.
- [ ] Backend citation contract tests cover document citations.
- [ ] Frontend rendering tests cover document source chips.

## Blocked by

- Blocked by `issues/039-route-metadata-badge-contract.md`
- Blocked by `issues/040-chart-answer-source-chips.md`

## User stories addressed

- User story 10
- User story 11
- User story 31
- User story 36
- User story 37
