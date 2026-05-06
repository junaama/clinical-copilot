"""Guideline citations survive the wire path (issue 027).

Issue 027 carries ``guideline:{chunk_id}`` references through the full
RAG response path: worker output → verifier ratification → backend block
construction → API response → frontend rendering. The previous code path
stripped ``<cite/>`` tags into clean prose but produced an empty
``citations`` tuple on the resulting ``PlainBlock``, so the frontend had
nothing to render.

These tests pin the contract:

* The citation-card mapper recognises the ``guideline:`` prefix and
  collapses it onto a non-chart ``"guideline"`` card.
* ``build_citations`` produces ratified ``Citation`` objects for
  guideline refs, with a label that surfaces the source guideline and
  section when the ``<cite/>`` tag carried them.
* ``plain_block_from_text`` accepts pre-built citations and forwards
  them onto the resulting ``PlainBlock`` so the verifier's plain-block
  path no longer drops citations.
"""

from __future__ import annotations

from copilot.api.schemas import fhir_ref_to_card
from copilot.blocks import (
    build_citations,
    extract_cite_attributes,
    plain_block_from_text,
)


def test_fhir_ref_to_card_maps_guideline_prefix_to_guideline_card() -> None:
    assert fhir_ref_to_card("guideline:jnc8-step2") == "guideline"


def test_fhir_ref_to_card_handles_guideline_with_extra_path_separators() -> None:
    """Some chunk ids embed ``/`` in their path; the mapper still routes."""
    assert fhir_ref_to_card("guideline:ada/2024/a1c-target") == "guideline"


def test_fhir_ref_to_card_existing_chart_refs_unchanged() -> None:
    assert fhir_ref_to_card("MedicationRequest/m1") == "medications"
    assert fhir_ref_to_card("DocumentReference/d1") == "documents"


def test_extract_cite_attributes_returns_source_and_section_for_guideline() -> None:
    text = (
        "JNC 8 recommends thiazide first-line "
        '<cite ref="guideline:jnc8-step2" source="JNC8" section="4.1"/>.'
    )
    attrs = extract_cite_attributes(text)
    assert attrs["guideline:jnc8-step2"] == {
        "source": "JNC8",
        "section": "4.1",
    }


def test_extract_cite_attributes_returns_empty_dict_for_simple_ref() -> None:
    text = 'BP 90/60 <cite ref="Observation/obs-bp-2"/> at 03:14.'
    attrs = extract_cite_attributes(text)
    assert attrs == {"Observation/obs-bp-2": {}}


def test_build_citations_produces_guideline_card_for_guideline_ref() -> None:
    citations = build_citations(
        cited_refs=["guideline:jnc8-step2"],
        fetched_refs=["guideline:jnc8-step2"],
    )
    assert len(citations) == 1
    citation = citations[0]
    assert citation.card == "guideline"
    assert citation.fhir_ref == "guideline:jnc8-step2"
    # Default label without attribute hints still says ``guideline``
    # so the chip is not blank.
    assert "guideline" in citation.label.lower()


def test_build_citations_uses_source_and_section_for_guideline_label() -> None:
    citations = build_citations(
        cited_refs=["guideline:jnc8-step2"],
        fetched_refs=["guideline:jnc8-step2"],
        cite_attributes={
            "guideline:jnc8-step2": {"source": "JNC 8", "section": "4.1"},
        },
    )
    assert len(citations) == 1
    label = citations[0].label
    assert "JNC 8" in label
    assert "4.1" in label


def test_build_citations_drops_unfetched_guideline_refs() -> None:
    citations = build_citations(
        cited_refs=["guideline:real", "guideline:hallucinated"],
        fetched_refs=["guideline:real"],
    )
    assert len(citations) == 1
    assert citations[0].fhir_ref == "guideline:real"


def test_plain_block_from_text_carries_pre_built_citations() -> None:
    text = (
        "ADA suggests an A1c target of <7% for most adults "
        '<cite ref="guideline:ada-a1c-2024-1" source="ADA" section="6.5"/>.'
    )
    citations = build_citations(
        cited_refs=["guideline:ada-a1c-2024-1"],
        fetched_refs=["guideline:ada-a1c-2024-1"],
        cite_attributes=extract_cite_attributes(text),
    )

    block = plain_block_from_text(text, citations=citations)
    assert block.kind == "plain"
    assert "<cite" not in block.lead
    assert len(block.citations) == 1
    assert block.citations[0].card == "guideline"
    assert block.citations[0].fhir_ref == "guideline:ada-a1c-2024-1"
