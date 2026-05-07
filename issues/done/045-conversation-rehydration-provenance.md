## Parent PRD

`issues/prd.md`

## What to build

Preserve structured provenance when loading saved conversations. Reloaded or
sidebar-selected conversations should restore route labels, citation chips, and
other structured answer state instead of flattening prior assistant messages
into plain uncited text.

## Acceptance criteria

- [ ] Persisted assistant turns can retain or reconstruct their structured
      block, route metadata, and citation metadata.
- [ ] Conversation rehydration restores route labels for prior assistant
      answers.
- [ ] Conversation rehydration restores source chips for prior cited answers.
- [ ] Legacy conversations without structured metadata still render safely as
      plain text.
- [ ] Backend conversation endpoint tests cover structured assistant turns.
- [ ] Frontend rehydration tests cover route and citation preservation.

## Blocked by

- Blocked by `issues/039-route-metadata-badge-contract.md`
- Blocked by `issues/040-chart-answer-source-chips.md`

## User stories addressed

- User story 25
- User story 26
- User story 32
- User story 36
- User story 37
