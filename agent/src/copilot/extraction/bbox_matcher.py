"""Locate VLM-extracted values inside the source PDF.

Pipeline:
    extraction object  ──┐
                         ├──► (field_path, value) pairs
    PDF bytes ──► PyMuPDF page.get_text("words") ──► word-level geometry
                         │
                         ▼
              fuzzy-match each value against contiguous word
              spans on each page; emit ``FieldWithBBox`` with
              normalized 0-1 coordinates.

Matching:
    * Similarity is ``difflib.SequenceMatcher.ratio()`` over the
      lowercased value vs. a candidate span. The PRD calls for
      "normalized Levenshtein distance <= 0.2"; ratio = 1 - distance,
      so the equivalent threshold is ``ratio ≥ 0.8``.
    * Sliding window over consecutive words on the same line, then
      across adjacent lines. Window length ranges from ``len(words)``
      to ``len(words) + 2`` to absorb minor OCR splits.
    * Multi-match disambiguation: for a logical group (lab result with
      ``test_name`` + ``value`` + ``unit``), pick the candidate closest
      to siblings already matched in the same group. This is a soft
      signal — fields with no group context just take the highest score.
    * No match: emit ``FieldWithBBox`` with ``bbox=None`` and
      ``match_confidence=highest_observed_score``. Callers fall back
      to a page-level citation.

Non-PDF inputs (PNG/JPEG): PyMuPDF cannot pull text geometry from raster
images we did not OCR, so we return one ``FieldWithBBox`` per extraction
field with ``bbox=None`` and ``match_confidence=0.0``. Callers should
emit a page-level citation (page=1) when they see this.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import fitz  # PyMuPDF
from pydantic import BaseModel

from copilot.extraction.schemas import BoundingBox, FieldWithBBox, VlmBoundingBox

_log = logging.getLogger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.8

# Field paths whose values are derived (not literally present in the
# source) and would just produce noisy false-positive matches. We still
# emit ``FieldWithBBox`` entries for them, but always with ``bbox=None``.
_DERIVED_PATH_SUFFIXES: frozenset[str] = frozenset(
    {
        "abnormal_flag",
        "confidence",
        "extraction_model",
        "extraction_timestamp",
        "source_document_id",
        "match_confidence",
        # Source-citation envelope fields are populated by the agent, not
        # the document — matching them against OCR spans only produces
        # false positives.
        "source_type",
        "source_id",
        "page_or_section",
        "field_or_chunk_id",
    }
)


@dataclass(frozen=True, slots=True)
class _Word:
    """One PyMuPDF word: text + page-absolute bbox in PDF points."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    line_key: tuple[int, int]  # (block_no, line_no) — for line-aware windowing


@dataclass(frozen=True, slots=True)
class _Page:
    """One PDF page worth of words plus its dimensions."""

    page: int  # 1-indexed
    width: float
    height: float
    words: tuple[_Word, ...]


@dataclass(frozen=True, slots=True)
class _Match:
    """Best fuzzy match for one extracted value."""

    page: int
    page_width: float
    page_height: float
    x0: float
    y0: float
    x1: float
    y1: float
    matched_text: str
    score: float


def _extract_vlm_bboxes(
    extraction: BaseModel | dict[str, Any] | list[Any],
) -> dict[str, VlmBoundingBox]:
    """Extract VLM-emitted bboxes from the extraction, keyed by result group prefix.

    For a ``LabExtraction`` with ``results[i].vlm_bbox``, returns a dict
    mapping ``"results[i]"`` → ``VlmBoundingBox``. The bbox matcher uses
    this to look up VLM-native coordinates for any field under that result.
    """

    if isinstance(extraction, BaseModel):
        data: Any = extraction.model_dump(mode="python")
    else:
        data = extraction

    bboxes: dict[str, VlmBoundingBox] = {}
    if not isinstance(data, dict):
        return bboxes

    results = data.get("results")
    if not isinstance(results, list):
        return bboxes

    for i, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        vlm_bbox_data = result.get("vlm_bbox")
        if vlm_bbox_data is None:
            continue
        try:
            vlm_bbox = VlmBoundingBox.model_validate(vlm_bbox_data)
            bboxes[f"results[{i}]"] = vlm_bbox
        except Exception:
            _log.debug("results[%d].vlm_bbox failed validation, skipping", i)

    return bboxes


def _validate_vlm_bbox(vlm_bbox: VlmBoundingBox) -> str | None:
    """Return ``None`` if the VLM bbox is valid for use, or a reason string if not.

    Checks:
    - All four coordinates are in [0, 1] range
    - The box has non-zero area (x1 > x0, y1 > y0)
    - The box has plausible placement (not degenerate or implausibly small)
    """

    x0, y0, x1, y1 = vlm_bbox.bbox

    # Check bounds: all coordinates must be in [0, 1]
    for coord_name, coord in [("x0", x0), ("y0", y0), ("x1", x1), ("y1", y1)]:
        if coord < 0.0 or coord > 1.0:
            return f"{coord_name}={coord} out of [0, 1] bounds"

    # Check non-zero area
    width = x1 - x0
    height = y1 - y0
    if width <= 0.0:
        return f"zero or negative width: x1-x0={width}"
    if height <= 0.0:
        return f"zero or negative height: y1-y0={height}"

    # Check plausible placement (box must have at least minimal area)
    area = width * height
    if area < 1e-6:
        return f"implausibly small area: {area}"

    return None


def match_extraction_to_bboxes(
    extraction: BaseModel | dict[str, Any] | list[Any],
    pdf_bytes: bytes,
    *,
    mimetype: str | None = None,
    similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
) -> list[FieldWithBBox]:
    """Return one ``FieldWithBBox`` per string-leaf in ``extraction``.

    Args:
        extraction: a Pydantic model, dict, or list. String leaves are
            walked recursively and become candidates for matching.
        pdf_bytes: source document bytes. PDFs get word-level geometry;
            PNG/JPEG and any other content type fall through to
            page-level citations (``bbox=None``).
        mimetype: optional content type hint. When ``None`` the function
            attempts to open the bytes as a PDF; it falls back to the
            no-bbox path on any PyMuPDF failure.
        similarity_threshold: minimum SequenceMatcher ratio for a match
            to be accepted. Defaults to 0.8 (= Levenshtein distance ≤ 0.2).

    Returns:
        A list of ``FieldWithBBox`` in the same order as the walk.
    """
    fields = _collect_fields(extraction)
    vlm_bboxes = _extract_vlm_bboxes(extraction)
    pages = _read_pdf_pages(pdf_bytes, mimetype=mimetype)

    if not pages:
        return [
            FieldWithBBox(
                field_path=path,
                extracted_value=value,
                matched_text="",
                bbox=None,
                match_confidence=0.0,
            )
            for path, value in fields
        ]

    matched_so_far: list[_Match] = []
    out: list[FieldWithBBox] = []
    for path, value in fields:
        if _is_derived_path(path) or not value.strip():
            out.append(
                FieldWithBBox(
                    field_path=path,
                    extracted_value=value,
                    matched_text="",
                    bbox=None,
                    match_confidence=0.0,
                )
            )
            continue

        # Check for a VLM-native bbox from the parent result group.
        group = _group_prefix(path)
        vlm_bbox = vlm_bboxes.get(group) if group else None
        if vlm_bbox is not None:
            reason = _validate_vlm_bbox(vlm_bbox)
            if reason is None:
                # VLM bbox is valid — use it as the primary coordinate source.
                x0, y0, x1, y1 = vlm_bbox.bbox
                _log.debug(
                    "bbox_source=vlm for %s (page=%d, coords=[%.3f,%.3f,%.3f,%.3f])",
                    path,
                    vlm_bbox.page,
                    x0,
                    y0,
                    x1,
                    y1,
                )
                out.append(
                    FieldWithBBox(
                        field_path=path,
                        extracted_value=value,
                        matched_text=value,
                        bbox=BoundingBox(
                            page=vlm_bbox.page,
                            x=x0,
                            y=y0,
                            width=max(x1 - x0, 1e-6),
                            height=max(y1 - y0, 1e-6),
                        ),
                        match_confidence=1.0,
                        bbox_source="vlm",
                    )
                )
                continue
            _log.debug(
                "bbox_source=pymupdf for %s (vlm_bbox rejected: %s)",
                path,
                reason,
            )

        # Fall back to PyMuPDF word-geometry matching.
        match = _find_best_match(
            value=value,
            pages=pages,
            siblings=_siblings(path, matched_so_far, out),
            similarity_threshold=similarity_threshold,
        )

        if match is None or match.score < similarity_threshold:
            out.append(
                FieldWithBBox(
                    field_path=path,
                    extracted_value=value,
                    matched_text=match.matched_text if match else "",
                    bbox=None,
                    match_confidence=match.score if match else 0.0,
                )
            )
            continue

        matched_so_far.append(match)
        out.append(
            FieldWithBBox(
                field_path=path,
                extracted_value=value,
                matched_text=match.matched_text,
                bbox=BoundingBox(
                    page=match.page,
                    x=match.x0 / match.page_width,
                    y=match.y0 / match.page_height,
                    width=max(
                        (match.x1 - match.x0) / match.page_width,
                        1e-6,
                    ),
                    height=max(
                        (match.y1 - match.y0) / match.page_height,
                        1e-6,
                    ),
                ),
                match_confidence=match.score,
                bbox_source="pymupdf",
            )
        )

    return out


def _collect_fields(
    extraction: BaseModel | dict[str, Any] | list[Any],
) -> list[tuple[str, str]]:
    """Walk the extraction and yield ``(field_path, string_value)`` pairs."""
    if isinstance(extraction, BaseModel):
        data: Any = extraction.model_dump(mode="python")
    else:
        data = extraction
    out: list[tuple[str, str]] = []
    _walk(data, "", out)
    return out


def _walk(node: Any, prefix: str, out: list[tuple[str, str]]) -> None:
    if isinstance(node, str):
        out.append((prefix or "value", node))
        return
    if isinstance(node, dict):
        for key, value in node.items():
            sub = f"{prefix}.{key}" if prefix else str(key)
            _walk(value, sub, out)
        return
    if isinstance(node, list):
        for idx, value in enumerate(node):
            sub = f"{prefix}[{idx}]"
            _walk(value, sub, out)
        return
    # numbers, bools, None — nothing to match


def _is_derived_path(path: str) -> bool:
    tail = path.rsplit(".", 1)[-1]
    return tail in _DERIVED_PATH_SUFFIXES


def _read_pdf_pages(
    pdf_bytes: bytes,
    *,
    mimetype: str | None,
) -> list[_Page]:
    if mimetype and mimetype.lower() in {"image/png", "image/jpeg", "image/jpg"}:
        return []
    if not pdf_bytes:
        return []
    try:
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
    except Exception:
        return []

    pages: list[_Page] = []
    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            rect = page.rect
            words_raw = page.get_text("words")
            words = tuple(
                _Word(
                    text=str(w[4]),
                    x0=float(w[0]),
                    y0=float(w[1]),
                    x1=float(w[2]),
                    y1=float(w[3]),
                    line_key=(int(w[5]), int(w[6])),
                )
                for w in words_raw
                if str(w[4]).strip()
            )
            pages.append(
                _Page(
                    page=page_index + 1,
                    width=float(rect.width) or 1.0,
                    height=float(rect.height) or 1.0,
                    words=words,
                )
            )
    finally:
        doc.close()
    return pages


def _siblings(
    path: str,
    matched: list[_Match],
    emitted: list[FieldWithBBox],
) -> list[_Match]:
    """Return previously-matched fields that share a logical group prefix.

    Group prefix = everything up to and including the last ``[N]``. For
    paths without an index (e.g., top-level demographic fields) we use
    the empty prefix, which yields all currently-matched fields — a
    cheap heuristic that keeps neighboring lab results close together.
    """
    group = _group_prefix(path)
    if not matched:
        return []
    if not group:
        return list(matched)
    # find indices in `emitted` whose path shares the group prefix
    group_paths: set[str] = {fb.field_path for fb in emitted if fb.field_path.startswith(group)}
    # matched is a subset of emitted in walk order; filter by path identity
    out: list[_Match] = []
    for fb, match in zip(
        (fb for fb in emitted if fb.bbox is not None),
        matched,
        strict=True,
    ):
        if fb.field_path in group_paths:
            out.append(match)
    return out


def _group_prefix(path: str) -> str:
    """Return the path up to and including the last ``[N]`` segment."""
    last_close = path.rfind("]")
    if last_close == -1:
        return ""
    return path[: last_close + 1]


def _find_best_match(
    *,
    value: str,
    pages: list[_Page],
    siblings: list[_Match],
    similarity_threshold: float,
) -> _Match | None:
    target = _normalize(value)
    if not target:
        return None
    target_word_count = max(len(target.split()), 1)

    best: _Match | None = None
    for page in pages:
        page_best = _best_on_page(
            page=page,
            target=target,
            target_word_count=target_word_count,
            siblings=siblings,
            similarity_threshold=similarity_threshold,
        )
        if page_best is None:
            continue
        if best is None or _is_better(page_best, best, siblings):
            best = page_best
    return best


def _best_on_page(
    *,
    page: _Page,
    target: str,
    target_word_count: int,
    siblings: list[_Match],
    similarity_threshold: float,
) -> _Match | None:
    if not page.words:
        return None
    words = page.words
    n = len(words)
    # Window sizes flex around the target word count to absorb OCR splits.
    sizes = sorted({
        max(1, target_word_count - 1),
        target_word_count,
        target_word_count + 1,
        target_word_count + 2,
    })

    best: _Match | None = None
    for size in sizes:
        if size > n:
            continue
        for start in range(0, n - size + 1):
            window = words[start : start + size]
            # Skip windows that span more than two adjacent lines — keeps
            # us from gluing unrelated fields together.
            if not _on_same_line_block(window):
                continue
            text = " ".join(w.text for w in window)
            score = _similarity(target, _normalize(text))
            if score < similarity_threshold and (best is None or score <= best.score):
                continue
            x0 = min(w.x0 for w in window)
            y0 = min(w.y0 for w in window)
            x1 = max(w.x1 for w in window)
            y1 = max(w.y1 for w in window)
            candidate = _Match(
                page=page.page,
                page_width=page.width,
                page_height=page.height,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                matched_text=text,
                score=score,
            )
            if best is None or _is_better(candidate, best, siblings):
                best = candidate
    return best


def _on_same_line_block(window: tuple[_Word, ...]) -> bool:
    """Allow at most two distinct line keys in one window."""
    keys = {w.line_key for w in window}
    return len(keys) <= 2


def _is_better(candidate: _Match, current: _Match, siblings: list[_Match]) -> bool:
    if candidate.score > current.score + 1e-6:
        return True
    if candidate.score < current.score - 1e-6:
        return False
    # Tie-break on proximity to siblings (closer = better).
    if not siblings:
        return False
    cand_d = _min_sibling_distance(candidate, siblings)
    curr_d = _min_sibling_distance(current, siblings)
    return cand_d < curr_d


def _min_sibling_distance(match: _Match, siblings: list[_Match]) -> float:
    cx, cy = (match.x0 + match.x1) / 2, (match.y0 + match.y1) / 2
    best = float("inf")
    for sib in siblings:
        if sib.page != match.page:
            continue
        sx, sy = (sib.x0 + sib.x1) / 2, (sib.y0 + sib.y1) / 2
        d = ((cx - sx) ** 2 + (cy - sy) ** 2) ** 0.5
        if d < best:
            best = d
    return best


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()
