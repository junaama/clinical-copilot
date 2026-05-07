## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Add a demoable source-grounding path for image-backed uploads. After a clinician
uploads a PNG or JPEG and extraction returns drawable bbox records, the
extraction panel should offer Results and Source tabs. Clinically important
fields with exact bbox path matches should expose a source action in Results.
Selecting that action should switch to Source, display the uploaded image from
the browser-local file object, show all boxes faintly, and highlight the
selected box prominently.

## Acceptance criteria

- [ ] The extraction panel renders Results and Source tabs.
- [ ] The upload flow carries the browser-local uploaded file or object URL
      forward so the Source tab can render a preview without a new backend
      download endpoint.
- [ ] Image uploads render in the Source tab with normalized bbox overlays.
- [ ] All drawable bboxes render as faint source highlights.
- [ ] The selected bbox renders with a visually prominent style.
- [ ] Results rows/fields expose a source action only when an exact
      `field_path` match exists in the bbox map.
- [ ] Selecting a source action switches to Source and highlights the matching
      bbox.
- [ ] Fields without drawable bbox matches do not expose a source CTA.
- [ ] Component tests cover tab switching, exact-path source CTA visibility,
      source selection, and image overlay rendering.

## Blocked by

- Blocked by `issues/031-upload-bbox-response-contract.md`

## User stories addressed

- User story 3
- User story 4
- User story 6
- User story 7
- User story 8
- User story 18
- User story 26
