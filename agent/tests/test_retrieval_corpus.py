"""Unit tests for ``copilot.retrieval.corpus``.

These exercise external behavior — section detection on representative
heading shapes, chunk-window math, deterministic chunk_id hashing, and
the bundled fixture PDFs end-to-end. No DB, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.retrieval.corpus import (
    GuidelineChunk,
    PageText,
    Section,
    _chunk_id,
    _looks_like_heading,
    chunk_guideline,
    chunk_sections,
    chunk_text,
    detect_sections,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDELINES_DIR = REPO_ROOT / "data" / "guidelines"


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "1. INTRODUCTION",
        "4. CHOICE OF INITIAL ANTIHYPERTENSIVE THERAPY",
        "4.2 Treatment goals",
        "4.2.3 Add-on therapy",
        "Section 4 Add-on therapy",
        "CHAPTER 1. EVALUATION OF CKD",
        "TREATMENT GUIDELINES",
        "A. ASSESSMENT OF GLYCEMIC CONTROL",
    ],
)
def test_looks_like_heading_positive(line: str) -> None:
    assert _looks_like_heading(line)


@pytest.mark.parametrize(
    "line",
    [
        "150 mm Hg or higher or diastolic blood pressure (DBP) is 90 mm Hg or higher,",
        "and treat to a goal SBP below 150 mm Hg and a goal DBP below",
        "",
        "  ",
        # Trailing comma — body text continuation.
        "1. The first finding,",
        # Way too long.
        "INTRODUCTION " + "x" * 100,
        # All-caps but too few letters total.
        "A B",
    ],
)
def test_looks_like_heading_negative(line: str) -> None:
    assert not _looks_like_heading(line)


def test_detect_sections_falls_back_to_per_page_when_no_headings() -> None:
    pages = [
        PageText(page=1, text="just some prose that has no headings at all in it.\nMore prose."),
        PageText(page=2, text="continuing prose; no headings here either."),
        PageText(page=3, text=""),  # empty page should be skipped
    ]
    sections = detect_sections(pages)
    assert len(sections) == 2
    assert sections[0].name == "page 1"
    assert sections[0].start_page == sections[0].end_page == 1
    assert sections[1].name == "page 2"


def test_detect_sections_splits_at_headings() -> None:
    pages = [
        PageText(
            page=1,
            text=(
                "1. INTRODUCTION\n"
                "Body text for intro section.\n"
                "Continued.\n"
                "2. METHODS\n"
                "Body text for methods."
            ),
        ),
        PageText(
            page=2,
            text=("More methods text.\n" "3. RESULTS\n" "Body text for results."),
        ),
    ]
    sections = detect_sections(pages)
    names = [s.name for s in sections]
    assert names == ["1. INTRODUCTION", "2. METHODS", "3. RESULTS"]
    intro = sections[0]
    assert intro.start_page == intro.end_page == 1
    methods = sections[1]
    # Methods starts on page 1 (heading on page 1) and continues onto page 2.
    assert methods.start_page == 1
    assert methods.end_page == 2
    assert "Body text for methods." in methods.text
    assert "More methods text." in methods.text
    results = sections[2]
    assert results.start_page == results.end_page == 2


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_chunk_text_empty_returns_empty() -> None:
    assert chunk_text("") == []


def test_chunk_text_single_window_when_under_limit() -> None:
    text = " ".join(["word"] * 50)
    chunks = chunk_text(text, max_tokens=512, overlap=64)
    assert chunks == [text]


def test_chunk_text_overlapping_windows() -> None:
    # 1000 unique tokens so we can verify boundaries by inspection.
    tokens = [f"t{i}" for i in range(1000)]
    chunks = chunk_text(" ".join(tokens), max_tokens=200, overlap=50)
    # stride = 150; windows starting at 0, 150, 300, 450, 600, 750, 900
    assert len(chunks) == 7
    first = chunks[0].split()
    second = chunks[1].split()
    assert first[0] == "t0"
    assert first[-1] == "t199"
    assert second[0] == "t150"
    assert second[-1] == "t349"
    # Last chunk ends at the last token.
    assert chunks[-1].split()[-1] == "t999"


def test_chunk_text_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("hello world", max_tokens=10, overlap=10)
    with pytest.raises(ValueError):
        chunk_text("hello world", max_tokens=10, overlap=-1)
    with pytest.raises(ValueError):
        chunk_text("hello world", max_tokens=0, overlap=0)


def test_chunk_id_is_deterministic_and_distinct() -> None:
    a = _chunk_id("guide", "sec", 1, "content one")
    b = _chunk_id("guide", "sec", 1, "content one")
    c = _chunk_id("guide", "sec", 1, "content two")
    d = _chunk_id("guide", "sec", 2, "content one")
    e = _chunk_id("other", "sec", 1, "content one")
    assert a == b
    assert len({a, c, d, e}) == 4  # all distinct
    assert len(a) == 32


def test_chunk_sections_attaches_metadata() -> None:
    sections = [
        Section(name="1. INTRO", start_page=1, end_page=1, text=" ".join(["w"] * 100)),
        Section(name="2. METHODS", start_page=2, end_page=3, text=" ".join(["x"] * 600)),
    ]
    chunks = chunk_sections(sections, "myguide", max_tokens=512, overlap=64)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.guideline == "myguide"
        assert c.section in {"1. INTRO", "2. METHODS"}
        assert c.page in {1, 2, 3}
        assert c.chunk_id and len(c.chunk_id) == 32


def test_chunk_sections_dedupes_identical_chunks() -> None:
    # Two sections with identical names + content + start page produce
    # the same chunk_id and should collapse to one row.
    sections = [
        Section(name="sec", start_page=1, end_page=1, text="alpha beta gamma"),
        Section(name="sec", start_page=1, end_page=1, text="alpha beta gamma"),
    ]
    chunks = chunk_sections(sections, "g", max_tokens=512, overlap=0)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# Fixture-PDF end-to-end (uses the bundled data/guidelines/*.pdf)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not GUIDELINES_DIR.exists(), reason="data/guidelines/ not present in this checkout"
)
@pytest.mark.parametrize(
    "filename, expected_section_substr",
    [
        ("jnc8-hypertension-2014.pdf", "PHARMACOLOGIC"),
        ("ada-diabetes-glycemic-2024.pdf", "GLYCEMIC"),
        ("kdigo-ckd-2024.pdf", "CKD"),
    ],
)
def test_fixture_pdf_chunks_end_to_end(filename: str, expected_section_substr: str) -> None:
    pdf = GUIDELINES_DIR / filename
    if not pdf.exists():
        pytest.skip(f"{pdf} not present")

    chunks = chunk_guideline(pdf, filename[:-4])
    assert chunks, f"no chunks produced for {filename}"
    for c in chunks:
        assert isinstance(c, GuidelineChunk)
        assert c.guideline == filename[:-4]
        assert c.page >= 1
        assert c.content
    assert any(expected_section_substr in c.section for c in chunks)


@pytest.mark.skipif(
    not (GUIDELINES_DIR / "jnc8-hypertension-2014.pdf").exists(),
    reason="JNC8 fixture not present",
)
def test_fixture_pdf_chunks_are_idempotent() -> None:
    pdf = GUIDELINES_DIR / "jnc8-hypertension-2014.pdf"
    first = chunk_guideline(pdf, "jnc8-hypertension-2014")
    second = chunk_guideline(pdf, "jnc8-hypertension-2014")
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
