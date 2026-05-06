## Parent PRD

`issues/prd-doc-recovery.md`

## What to build

Carry guideline citations through the full RAG response path. Retrieved
`guideline:` references should survive worker output, verifier ratification,
backend block construction, API parsing, and frontend rendering as visible
source chips or equivalent markers. Guideline citations should not be forced
through the FHIR/chart-card citation model.

The completed slice should be demoable by asking ADA or KDIGO questions and
seeing visible guideline source chips that identify the guideline and section
when available.

## Acceptance criteria

- [ ] Retrieved guideline chunks expose stable `guideline:` refs in fetched
      refs for verifier validation.
- [ ] Final supervisor/RAG answers with guideline cite tags produce response
      citation objects instead of dropping citations while stripping tags.
- [ ] Guideline citation objects are modeled as first-class non-chart citations
      and do not imply chart-card navigation.
- [ ] Frontend response parsing accepts guideline source citations without
      widening unrelated chart citation behavior.
- [ ] Plain agent messages render guideline citations after streaming completes.
- [ ] Existing FHIR/document citation chips continue to render and navigate as
      before.
- [ ] Tests cover backend block construction, response parsing, Agent message
      rendering, and no chart-card highlight attempt for guideline citations.

## Blocked by

None - can start immediately.

## User stories addressed

Reference by number from the parent PRD:

- User story 17
- User story 18
- User story 20
- User story 21
- User story 22
- User story 28
