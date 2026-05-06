## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Create the VLM extraction pipeline that converts PDF/image documents into structured data using Claude Sonnet 4 vision. This module handles PDF-to-image conversion, prompt construction, structured output parsing, and confidence scoring.

**Module:** `agent/src/copilot/extraction/vlm.py`

**Pipeline:**
1. Accept file bytes + mimetype
2. If PDF: split into per-page images using PyMuPDF (`fitz`)
3. If PNG/JPEG: use directly as single page
4. For each page image: call Claude Sonnet 4 (vision) with a structured output prompt targeting the appropriate schema (lab_pdf or intake_form)
5. Parse VLM response into the Pydantic schema from issue 002
6. Return validated extraction OR validation error + raw response

**Key design points:**
- Single-shot structured output per page (no multi-turn, no tool use in the VLM call)
- The prompt includes the target schema as a JSON schema definition
- Per-field `confidence` (high/medium/low) is part of the prompt instruction
- Multi-page PDFs are processed page-by-page; results are merged into a single extraction
- `build_vision_model()` factory function added to `llm.py` for the multimodal-capable model

## Acceptance criteria

- [ ] PDF → page images conversion works (PyMuPDF)
- [ ] PNG/JPEG passthrough works (single-page extraction)
- [ ] VLM prompt includes target schema and confidence instructions
- [ ] Response parsed into `LabExtraction` or `IntakeExtraction` (from issue 002 schemas)
- [ ] Multi-page PDF merges results across pages (lab results from page 1 and page 2 combined)
- [ ] Validation errors return both the error and the raw VLM response for debugging
- [ ] `build_vision_model()` added to `llm.py`
- [ ] Unit tests with mocked Anthropic API: valid extraction, invalid schema response, partial extraction with low-confidence fields
- [ ] Tested against at least 2 fixture documents from `example-documents/`

## Blocked by

- `issues/002-pydantic-schemas-validation.md` (schemas define the extraction target)

## User stories addressed

- User story 1 (upload and extract lab PDF)
- User story 5 (upload and extract intake form)
- User story 11 (flag low confidence values)
