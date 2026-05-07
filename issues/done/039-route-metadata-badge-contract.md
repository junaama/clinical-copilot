## Parent PRD

`issues/prd.md`

## What to build

Add the first end-to-end route transparency path from the chat response to the
visible UI. A normal chart answer should carry structured route metadata in the
API response, the frontend should parse it through the chat contract, and the
chat bubble/header should render a user-facing route label instead of relying
on answer prose.

## Acceptance criteria

- [ ] `/chat` responses include structured route metadata for a chart answer,
      including a stable route kind and user-facing label.
- [ ] The frontend parser accepts the documented route metadata shape and
      rejects malformed route metadata.
- [ ] A chart answer renders a visible route/status label in the chat UI.
- [ ] The agent header or answer surface no longer implies every answer is
      only "Reading this patient's record" when route metadata says otherwise.
- [ ] Backend and frontend contract tests cover the chart route happy path.

## Blocked by

None - can start immediately.

## User stories addressed

- User story 1
- User story 2
- User story 15
- User story 30
- User story 36
- User story 38
