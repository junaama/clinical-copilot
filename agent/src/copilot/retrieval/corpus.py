"""Guideline PDF loading + section-aware chunking.

Pure functions over filesystem and string inputs — no Postgres, no API
calls. The output is a list of ``GuidelineChunk`` records that
``indexer`` ships to Cohere + pgvector.

Token counting uses whitespace-split words as a proxy. This is precise
enough for the 512-token chunking criterion without dragging in
``tiktoken``: Cohere's tokenizer is unrelated to OpenAI's, and the
chunk size only needs to be in the right neighborhood for retrieval to
work well. If we later want true Cohere-token counts we can swap in
``cohere.tokenize`` without changing any callers.

Section detection is heuristic — lines that look like headings (short,
mostly capitalized, or numbered like ``4.2 Treatment``) start a new
section. When no headings are found in a document the chunker falls
back to per-page sections so retrieval citations still carry a page.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


# Chunking knobs. Pinned to the W2 PRD spec (512 / 64). Don't bump
# without re-running the indexer — chunk_id is content-derived so a size
# change creates new rows but doesn't delete the old ones.
DEFAULT_CHUNK_TOKENS = 512
DEFAULT_CHUNK_OVERLAP = 64

# A "heading" line is short. Longer lines are body text even if they
# happen to start with a numeral.
_HEADING_MAX_CHARS = 80

# Numbered section heading: "1.", "4.", "1.1", "4.2.3 Title", "Section 4 Title".
# We require one of the following so body lines starting with a value like
# "150 mm Hg ..." don't get picked up as headings:
#   * an explicit "Section " / "Chapter " / "Part " lead-in (any number form);
#   * a short trailing-period number ("1.", "12.");
#   * a multi-level dotted number ("4.2", "4.2.3").
_NUMBERED_HEADING_RE = re.compile(
    r"""
    ^\s*
    (?:
        (?:section|chapter|part)\s+\d+(?:\.\d+){0,2}\.?
        |
        \d+\.\d+(?:\.\d+){0,2}      # 4.2 or 4.2.3
        |
        \d{1,2}\.                   # 1. or 12. (short numbers only)
    )
    \s+
    (.+?)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# All-caps heading: "TREATMENT GUIDELINES", "CHAPTER 1. EVALUATION OF CKD".
# Allow letters, spaces, hyphens, ampersands, periods, and a small number of
# digits (so "CHAPTER 1." still matches). Require at least three uppercase
# letters total to dodge things like "1. A".
_ALLCAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9\s\-&/\.]{3,}$")


@dataclass(frozen=True)
class PageText:
    """One page's extracted text. ``page`` is 1-indexed for human-friendly
    citations (matches the page numbers the clinician reads off the PDF)."""

    page: int
    text: str


@dataclass(frozen=True)
class Section:
    """A logical document section spanning one or more pages.

    ``name`` is the heading text as it appeared in the document (or
    ``"page N"`` for the per-page fallback). ``start_page`` and
    ``end_page`` are inclusive 1-indexed page numbers.
    """

    name: str
    start_page: int
    end_page: int
    text: str


@dataclass(frozen=True)
class GuidelineChunk:
    """A single retrievable chunk. ``chunk_id`` is a stable content hash
    so re-running the indexer over an unchanged corpus is idempotent.
    """

    chunk_id: str
    guideline: str
    section: str
    page: int
    content: str


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def extract_pages(pdf_path: str | Path) -> list[PageText]:
    """Extract per-page text from a PDF using PyMuPDF.

    Returns the pages in document order with 1-indexed page numbers and
    the raw text from ``page.get_text()``. Empty / image-only pages are
    preserved with empty text so downstream page numbering stays aligned.
    """
    import fitz  # PyMuPDF; imported lazily so the import error is clear.

    path = Path(pdf_path)
    pages: list[PageText] = []
    with fitz.open(path) as doc:
        for idx, page in enumerate(doc, start=1):
            text = page.get_text() or ""
            pages.append(PageText(page=idx, text=text))
    return pages


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------


def _looks_like_heading(line: str) -> bool:
    """Return True when ``line`` looks like a section heading.

    Tight on length and punctuation so body-text false-positives (a
    numbered list item that happens to start with "4.", a sentence that
    starts with a value like "150 mm Hg ...") don't shred the document
    into thousands of one-line sections.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > _HEADING_MAX_CHARS:
        return False
    # Headings don't end mid-sentence: a trailing comma / semicolon /
    # colon almost always means body text.
    if stripped.endswith((",", ";", ":")):
        return False
    if _NUMBERED_HEADING_RE.match(stripped):
        return True
    if _ALLCAPS_HEADING_RE.match(stripped):
        # Require at least three actual letters so "1. A" doesn't match.
        letter_count = sum(1 for ch in stripped if ch.isalpha())
        return letter_count >= 3
    return False


def detect_sections(pages: list[PageText]) -> list[Section]:
    """Walk the pages and split into logical sections at heading lines.

    When no headings are found the document is split into per-page
    sections so every chunk still has a citeable page.
    """
    headings: list[tuple[int, str]] = []  # (page, heading text)

    # Precompute (page, line_index, line) so a heading on page 3 line 10
    # can be located precisely.
    flat: list[tuple[int, int, str]] = []
    for pg in pages:
        for i, line in enumerate(pg.text.splitlines()):
            flat.append((pg.page, i, line))
            if _looks_like_heading(line):
                headings.append((pg.page, line.strip()))

    if not headings:
        # Per-page fallback. Skips empty pages so we don't index nothing.
        return [
            Section(name=f"page {pg.page}", start_page=pg.page, end_page=pg.page, text=pg.text)
            for pg in pages
            if pg.text.strip()
        ]

    # Build sections by slicing flat between consecutive heading rows.
    # The slice from heading_i to heading_{i+1} (exclusive) is one
    # section; the final section runs to end-of-document.
    heading_rows: list[int] = []
    for ridx, (_, _, line) in enumerate(flat):
        if _looks_like_heading(line):
            heading_rows.append(ridx)

    sections: list[Section] = []
    for sidx, start_row in enumerate(heading_rows):
        end_row = heading_rows[sidx + 1] if sidx + 1 < len(heading_rows) else len(flat)
        heading_line = flat[start_row][2].strip()
        section_pages = flat[start_row + 1 : end_row]
        if not section_pages:
            continue
        body = "\n".join(line for _, _, line in section_pages)
        if not body.strip():
            continue
        start_page = flat[start_row][0]
        end_page = flat[end_row - 1][0] if end_row > 0 else start_page
        sections.append(
            Section(
                name=heading_line,
                start_page=start_page,
                end_page=end_page,
                text=body,
            )
        )

    return sections


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Whitespace tokenization. Cheap, deterministic, language-agnostic."""
    return text.split()


def chunk_text(
    text: str,
    *,
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Slide a window over ``text`` and return overlapping chunks.

    Each chunk is ``max_tokens`` tokens or fewer; consecutive chunks
    share ``overlap`` tokens to preserve context across the boundary.
    Empty input returns ``[]``.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap < 0 or overlap >= max_tokens:
        raise ValueError("overlap must be in [0, max_tokens)")

    tokens = _tokenize(text)
    if not tokens:
        return []

    stride = max_tokens - overlap
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk = " ".join(tokens[start:end])
        chunks.append(chunk)
        if end == len(tokens):
            break
        start += stride
    return chunks


def _chunk_page_for(
    section: Section,
    chunk_token_offset: int,
    chunk_token_count: int,
) -> int:
    """Best-effort page assignment for a chunk inside a multi-page section.

    Walks the section text page-by-page (sections preserve newline
    boundaries between pages, but we no longer carry per-page anchors —
    so we use the per-section span as a coarse approximation).

    For the per-page fallback (start_page == end_page) this collapses
    to the single page; for multi-page sections the chunk is attributed
    to ``start_page`` plus a fraction of the page span proportional to
    the chunk's offset into the section's token stream.
    """
    if section.start_page == section.end_page:
        return section.start_page

    section_tokens = _tokenize(section.text)
    if not section_tokens:
        return section.start_page

    midpoint = chunk_token_offset + chunk_token_count // 2
    fraction = min(1.0, midpoint / max(1, len(section_tokens)))
    span = section.end_page - section.start_page
    return section.start_page + round(fraction * span)


def _chunk_id(guideline: str, section: str, page: int, content: str) -> str:
    """Deterministic hash so re-runs of the indexer over the same input
    produce the same row keys (idempotency)."""
    digest = hashlib.sha256()
    digest.update(guideline.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(section.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(page).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content.encode("utf-8"))
    return digest.hexdigest()[:32]


def chunk_guideline(
    pdf_path: str | Path,
    guideline: str,
    *,
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[GuidelineChunk]:
    """Top-level: PDF → chunks ready for the indexer.

    Steps: extract pages → detect sections → chunk each section text
    with token-window overlap → assign pages → hash chunk_ids.
    """
    pages = extract_pages(pdf_path)
    sections = detect_sections(pages)
    return chunk_sections(sections, guideline, max_tokens=max_tokens, overlap=overlap)


def chunk_sections(
    sections: list[Section],
    guideline: str,
    *,
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[GuidelineChunk]:
    """Chunk an already-prepared section list. Carved out from
    ``chunk_guideline`` so tests can build sections by hand without a
    real PDF on disk."""
    out: list[GuidelineChunk] = []
    stride = max_tokens - overlap
    for section in sections:
        section_tokens = _tokenize(section.text)
        if not section_tokens:
            continue
        start = 0
        while start < len(section_tokens):
            end = min(start + max_tokens, len(section_tokens))
            content = " ".join(section_tokens[start:end])
            page = _chunk_page_for(section, start, end - start)
            cid = _chunk_id(guideline, section.name, page, content)
            out.append(
                GuidelineChunk(
                    chunk_id=cid,
                    guideline=guideline,
                    section=section.name,
                    page=page,
                    content=content,
                )
            )
            if end == len(section_tokens):
                break
            start += stride

    # Two chunks colliding on chunk_id means identical (guideline,
    # section, page, content). Deduplicate so the indexer doesn't have
    # to deal with conflicts on the PRIMARY KEY.
    seen: set[str] = set()
    deduped: list[GuidelineChunk] = []
    for c in out:
        if c.chunk_id in seen:
            continue
        seen.add(c.chunk_id)
        deduped.append(c)

    if len(deduped) != len(out):
        _log.debug("dropped %d duplicate chunks for %s", len(out) - len(deduped), guideline)

    return deduped


# ---------------------------------------------------------------------------
# Diagnostic helpers (used by the indexer CLI for a one-line summary)
# ---------------------------------------------------------------------------


def section_summary(sections: list[Section]) -> str:
    """Return a short one-line summary of the section breakdown."""
    pages = sum(s.end_page - s.start_page + 1 for s in sections)
    return f"{len(sections)} section(s) over {pages} page-span(s)"


def page_distribution(chunks: list[GuidelineChunk]) -> dict[int, int]:
    """Histogram of chunks-per-page; cheap sanity check for the indexer."""
    return dict(Counter(c.page for c in chunks))
