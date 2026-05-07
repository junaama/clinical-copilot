## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Extend the Source tab so PDF uploads are visually inspectable too. The viewer
should render the uploaded PDF locally in the browser, draw the same normalized
bbox overlay model used for images, and render the page containing the selected
bbox. This is intentionally narrower than a full PDF reader: it exists to prove
visual source grounding for the required lab PDF flow.

## Acceptance criteria

- [ ] PDF uploads render in the Source tab using browser-local file data.
- [ ] The selected bbox page is rendered when a source action is selected.
- [ ] The rendered PDF page displays all drawable bboxes for that page faintly.
- [ ] The selected bbox is highlighted prominently on the rendered PDF page.
- [ ] The Source tab displays a simple page label for the rendered page.
- [ ] The implementation does not require a new backend document-preview or
      document-download endpoint.
- [ ] Tests cover PDF source viewer behavior at a stable component boundary,
      using a mock or controlled renderer where full browser PDF rendering is
      not reliable in the test environment.
- [ ] UI build and tests continue to pass with the PDF rendering dependency.

## Blocked by

- Blocked by `issues/031-upload-bbox-response-contract.md`
- Blocked by `issues/032-source-tabs-with-image-overlay.md`

## User stories addressed

- User story 9
- User story 10
- User story 18
- User story 27
