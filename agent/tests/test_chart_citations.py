"""Chart citation labels survive the wire path (issue 040).

Issue 040 lands the medication-follow-up source-chip contract: a chart
medication answer must carry citation metadata for the supporting
``MedicationRequest`` / ``MedicationAdministration`` resources, with a
human-readable label (not the opaque ``"MedicationRequest (medications)"``
default) and the chart-card mapping the click-side flow needs.

These tests pin the backend half of the contract:

* ``build_citations`` produces a ``medications`` card for a
  ``MedicationRequest`` cite with the user-facing drug name surfaced from
  the ``<cite/>`` tag's ``name`` / ``dose`` attributes.
* When the LLM omits the medication name, the default chart-card label
  is human-readable ("Medication order") rather than the opaque resource-
  type juxtaposition.
* Other chart cards (vitals, labs, problems, allergies, encounters,
  documents, prescriptions, other) get human-readable defaults too — no
  card surfaces ``"<ResourceType> (<card>)"`` to the clinician.
* Absence markers in a cite attribute (``name="[not on file]"``) survive
  verbatim so a missing chart field reads as missing in returned source
  data, not as a fabricated default.
* The verifier's ``plain_block_from_text`` path forwards medication
  citations onto the resulting ``PlainBlock`` so the frontend has a
  source chip to render for chart medication answers.
"""

from __future__ import annotations

from copilot.api.schemas import fhir_ref_to_card
from copilot.blocks import (
    build_citations,
    extract_cite_attributes,
    plain_block_from_text,
)

# ---------------------------------------------------------------------------
# Citation-card mapping
# ---------------------------------------------------------------------------


def test_fhir_ref_to_card_maps_medication_request() -> None:
    assert fhir_ref_to_card("MedicationRequest/m1") == "medications"


def test_fhir_ref_to_card_maps_medication_administration() -> None:
    assert fhir_ref_to_card("MedicationAdministration/ma1") == "medications"


# ---------------------------------------------------------------------------
# Cite-attribute extraction
# ---------------------------------------------------------------------------


def test_extract_cite_attributes_returns_name_and_dose_for_medication() -> None:
    text = (
        "Active home metformin "
        '<cite ref="MedicationRequest/m1" name="metformin" dose="500 mg PO BID"/>.'
    )
    attrs = extract_cite_attributes(text)
    assert attrs["MedicationRequest/m1"] == {
        "name": "metformin",
        "dose": "500 mg PO BID",
    }


def test_extract_cite_attributes_preserves_absence_marker_in_attr() -> None:
    """An absence marker carried on a cite attribute survives extraction.

    The agent's medication tool surfaces missing dosage as ``[not specified
    on order]``; the LLM is told to surface the marker verbatim. When the
    citation chip echoes that marker, missing fields read as missing in the
    source, not as definitive absence.
    """
    text = (
        "metformin (no dose on order) "
        '<cite ref="MedicationRequest/m1" name="metformin" dose="[not specified on order]"/>.'
    )
    attrs = extract_cite_attributes(text)
    assert attrs["MedicationRequest/m1"]["dose"] == "[not specified on order]"


# ---------------------------------------------------------------------------
# Default labels — humanized for chart cards
# ---------------------------------------------------------------------------


def test_build_citations_uses_medication_name_and_dose_for_label() -> None:
    citations = build_citations(
        cited_refs=["MedicationRequest/m1"],
        fetched_refs=["MedicationRequest/m1"],
        cite_attributes={
            "MedicationRequest/m1": {
                "name": "metformin",
                "dose": "500 mg PO BID",
            },
        },
    )
    assert len(citations) == 1
    citation = citations[0]
    assert citation.card == "medications"
    assert citation.fhir_ref == "MedicationRequest/m1"
    assert "metformin" in citation.label
    assert "500 mg PO BID" in citation.label


def test_build_citations_uses_medication_name_only_when_dose_absent() -> None:
    citations = build_citations(
        cited_refs=["MedicationRequest/m1"],
        fetched_refs=["MedicationRequest/m1"],
        cite_attributes={"MedicationRequest/m1": {"name": "metformin"}},
    )
    assert len(citations) == 1
    label = citations[0].label
    assert "metformin" in label


def test_build_citations_falls_back_to_humanized_label_for_medication() -> None:
    """With no ``name`` attribute, the chip still reads as a chart source.

    The opaque ``"MedicationRequest (medications)"`` default fails the
    issue's "human-readable, avoid opaque-only identifiers" criterion. A
    medication chip without a name hint should still say ``Medication
    order`` so a clinician can recognize the source kind.
    """
    citations = build_citations(
        cited_refs=["MedicationRequest/m1"],
        fetched_refs=["MedicationRequest/m1"],
    )
    assert len(citations) == 1
    label = citations[0].label
    assert "MedicationRequest (medications)" not in label
    assert "Medication" in label


def test_build_citations_preserves_absence_marker_in_label() -> None:
    """An absence marker on the cite attribute reads through to the chip.

    The LLM is told to echo absence markers verbatim. If a medication's
    dose is missing on the order, the chip must say so rather than
    suppress the field — otherwise a clinician can't tell "not returned"
    from "definitely no dose."
    """
    citations = build_citations(
        cited_refs=["MedicationRequest/m1"],
        fetched_refs=["MedicationRequest/m1"],
        cite_attributes={
            "MedicationRequest/m1": {
                "name": "metformin",
                "dose": "[not specified on order]",
            },
        },
    )
    label = citations[0].label
    assert "metformin" in label
    assert "[not specified on order]" in label


def test_build_citations_humanizes_observation_vital_label() -> None:
    citations = build_citations(
        cited_refs=["Observation/obs-bp-2"],
        fetched_refs=["Observation/obs-bp-2"],
        observation_categories={"Observation/obs-bp-2": "vital-signs"},
    )
    label = citations[0].label
    assert "Observation (vitals)" not in label
    # Card-aware humanized default — clinician-readable.
    assert any(token in label for token in ("Vital", "vital"))


def test_build_citations_humanizes_observation_lab_label() -> None:
    citations = build_citations(
        cited_refs=["Observation/lab-1"],
        fetched_refs=["Observation/lab-1"],
        observation_categories={"Observation/lab-1": "laboratory"},
    )
    label = citations[0].label
    assert "Observation (labs)" not in label
    assert "Lab" in label


def test_build_citations_humanizes_problem_label() -> None:
    citations = build_citations(
        cited_refs=["Condition/c1"],
        fetched_refs=["Condition/c1"],
    )
    label = citations[0].label
    assert "Condition (problems)" not in label
    assert "Problem" in label


def test_build_citations_humanizes_allergy_label() -> None:
    citations = build_citations(
        cited_refs=["AllergyIntolerance/a1"],
        fetched_refs=["AllergyIntolerance/a1"],
    )
    label = citations[0].label
    assert "AllergyIntolerance (allergies)" not in label
    assert "Allergy" in label


def test_build_citations_humanizes_encounter_label() -> None:
    citations = build_citations(
        cited_refs=["Encounter/e1"],
        fetched_refs=["Encounter/e1"],
    )
    label = citations[0].label
    assert "Encounter (encounters)" not in label
    assert "Encounter" in label


def test_build_citations_humanizes_document_label() -> None:
    citations = build_citations(
        cited_refs=["DocumentReference/d1"],
        fetched_refs=["DocumentReference/d1"],
    )
    label = citations[0].label
    assert "DocumentReference (documents)" not in label
    assert "Document" in label


def test_build_citations_humanizes_prescription_label() -> None:
    citations = build_citations(
        cited_refs=["ServiceRequest/s1"],
        fetched_refs=["ServiceRequest/s1"],
    )
    label = citations[0].label
    assert "ServiceRequest (prescriptions)" not in label
    # ServiceRequest collapses to the prescriptions card; the label
    # should read as an order/prescription, not the FHIR resource type.
    assert "Order" in label or "Prescription" in label


# ---------------------------------------------------------------------------
# Plain-block forwarding for the verifier's chart-medication path
# ---------------------------------------------------------------------------


def test_plain_block_from_text_forwards_medication_citation_to_chip() -> None:
    """End-to-end: the verifier's plain-block path renders a med chip.

    Mirrors ``test_plain_block_from_text_carries_pre_built_citations``
    in ``test_guideline_citations.py`` but for the chart-medication path.
    The lead is cleaned of cite tags (per the contract) and the citation
    survives onto the block so the frontend has a source chip to render.
    """
    text = (
        "Active home medications include metformin "
        '<cite ref="MedicationRequest/m1" name="metformin" dose="500 mg PO BID"/>.'
    )
    citations = build_citations(
        cited_refs=["MedicationRequest/m1"],
        fetched_refs=["MedicationRequest/m1"],
        cite_attributes=extract_cite_attributes(text),
    )

    block = plain_block_from_text(text, citations=citations)
    assert block.kind == "plain"
    assert "<cite" not in block.lead
    assert len(block.citations) == 1
    cite = block.citations[0]
    assert cite.card == "medications"
    assert cite.fhir_ref == "MedicationRequest/m1"
    assert "metformin" in cite.label


def test_build_citations_drops_unfetched_medication_ref() -> None:
    """Defense-in-depth: an uncited (hallucinated) MedicationRequest is dropped.

    Mirrors the existing guideline-side test but for the medication card,
    so a chart medication answer that cites a med order the agent never
    actually fetched does not produce a misleading source chip.
    """
    citations = build_citations(
        cited_refs=["MedicationRequest/real", "MedicationRequest/hallucinated"],
        fetched_refs=["MedicationRequest/real"],
        cite_attributes={
            "MedicationRequest/real": {"name": "lisinopril"},
        },
    )
    assert len(citations) == 1
    assert citations[0].fhir_ref == "MedicationRequest/real"
    assert "lisinopril" in citations[0].label
