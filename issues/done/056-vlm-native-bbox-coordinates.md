## Parent PRD

`issues/prd.md`

## What to build

Switch the bounding-box overlay from PyMuPDF word-level geometry to
VLM-emitted normalized coordinates as the primary path. Keep
PyMuPDF as a validated secondary coordinate source so documents that
already overlay correctly do not regress and so VLM-emitted nonsense
(negative coords, out-of-bounds, zero-area, implausible placement)
doesn't ship to the user.

The change cuts through all layers: VLM prompt update, extraction
schema field, matcher selection logic, frontend overlay scaling,
and tests against rotated + multi-column fixtures.

See PRD §Implementation Decisions › VLM-Native Bounding-Box
Coordinates and §Testing Decisions › VLM-Native Bounding Boxes.

## Acceptance criteria

- [ ] VLM extraction prompt requires, alongside each cited literal
      value, `{ "page": int, "bbox": [x0, y0, x1, y1] }` with each
      coord in `[0, 1]` page-space
- [ ] Extraction schema gains a `vlm_bbox` field on the citation
      type; the existing PyMuPDF-derived bbox stays as
      `pymupdf_bbox`
- [ ] Matcher selection function prefers `vlm_bbox` when present
      and within `[0, 1]` bounds with non-zero area and plausible
      rendered-page placement; uses PyMuPDF otherwise; logs
      `bbox_source` and the reason VLM-native coordinates were not
      used
- [ ] Frontend overlay scales the selected bbox by rendered page
      width/height — no separate frontend redesign
- [ ] Tests in `agent/tests/extraction/test_bbox_matcher.py` cover
      VLM-native happy path, missing-vlm-bbox secondary-source
      selection, and invalid-vlm-bbox secondary-source selection
      (out-of-bounds, zero-area, implausible placement)
- [ ] Manual smoke against a rotated and a multi-column fixture
      from `example-documents/lab-results/` overlays correctly
      under the new path
- [ ] No regression on existing clean single-column documents (the
      smoke pass demonstrates this)
- [ ] `W2_ARCHITECTURE.md` bbox section describes VLM-native
      coordinates as primary, PyMuPDF as the validated secondary path
- [ ] Final checklist/demo captures evidence for rotated and
      multi-column overlay behavior, not just clean single-column
      PDFs

## Blocked by

None - can start immediately.

## User stories addressed

Reference by number from the parent PRD:

- User story 14
- User story 15
- User story 16
- User story 17
- User story 18
