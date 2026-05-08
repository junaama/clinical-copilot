"""Verifier accepts document and guideline citations with extra attributes.

Issue 009 introduces two new citation forms whose tags carry attributes
beyond ``ref``:

  <cite ref="DocumentReference/{id}" page="{n}" field="{path}" value="{lit}"/>
  <cite ref="guideline:{chunk_id}" source="{name}" section="{section}"/>

The previous ``_CITE_PATTERN`` in ``graph.py`` (and the matching ones in
``blocks.py`` / ``eval/faithfulness.py`` / ``eval/evaluators.py``)
required the closing ``>`` immediately after the ``ref="..."`` quote,
which silently fails on multi-attribute tags. The verifier would then
report unresolved citations even when the worker had populated
``fetched_refs`` with the document or guideline ref correctly.

These tests pin the contract: the regex must capture only the ``ref``
value regardless of trailing attributes.
"""

from __future__ import annotations

from copilot.blocks import _CITE_PATTERN as BLOCKS_CITE_PATTERN
from copilot.eval.evaluators import citation_resolution, extract_citations
from copilot.eval.faithfulness import _CITE_PATTERN as FAITHFULNESS_CITE_PATTERN
from copilot.graph import _extract_citations


def test_extract_citations_handles_document_reference_with_page_field_value() -> None:
    text = (
        "Total cholesterol 220 mg/dL "
        '<cite ref="DocumentReference/lab-001" page="1" field="results[0]" value="220"/>.'
    )
    assert _extract_citations(text) == ["DocumentReference/lab-001"]


def test_extract_citations_handles_guideline_with_source_and_section() -> None:
    text = (
        "JNC 8 recommends thiazide first-line "
        '<cite ref="guideline:jnc8-step2" source="JNC8" section="4.1"/>.'
    )
    assert _extract_citations(text) == ["guideline:jnc8-step2"]


def test_extract_citations_still_matches_simple_fhir_ref() -> None:
    text = 'BP 90/60 <cite ref="Observation/obs-bp-2"/> at 03:14.'
    assert _extract_citations(text) == ["Observation/obs-bp-2"]


def test_extract_citations_dedupes_repeated_ref_across_tags() -> None:
    text = (
        '<cite ref="DocumentReference/lab-001" value="220"/> and '
        '<cite ref="DocumentReference/lab-001" value="140"/>.'
    )
    assert _extract_citations(text) == ["DocumentReference/lab-001"]


def test_blocks_cite_pattern_handles_extra_attributes() -> None:
    text = '<cite ref="DocumentReference/lab-001" page="1" value="220"/>'
    matches = BLOCKS_CITE_PATTERN.findall(text)
    assert matches == ["DocumentReference/lab-001"]


def test_faithfulness_cite_pattern_handles_extra_attributes() -> None:
    text = '<cite ref="guideline:jnc8-step2" section="4.1"/>'
    matches = FAITHFULNESS_CITE_PATTERN.findall(text)
    assert matches == ["guideline:jnc8-step2"]


def test_evaluators_extract_citations_handles_extra_attributes() -> None:
    text = (
        '<cite ref="DocumentReference/lab-001" page="1" value="220"/> and '
        '<cite ref="guideline:abc" source="JNC8"/>.'
    )
    assert extract_citations(text) == [
        "DocumentReference/lab-001",
        "guideline:abc",
    ]


def test_citation_resolution_rejects_query_shaped_refs_even_if_fetched() -> None:
    ref = "Observation/_summary=count?patient=fixture-3"

    result = citation_resolution([ref], {ref})

    assert result["score"] == 0.0
    assert result["unresolved"] == [ref]
