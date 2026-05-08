"""Verifier rejects synthetic ``openemr-upload-<hex>`` document citations.

Issue 026: pre-issue-022 uploads under the OpenEMR bool-given 500 path
synthesized DocumentReference ids of the form ``openemr-upload-<sha-hex>``.
Those ids are not real OpenEMR resources. Issue 022 stopped producing
them in new uploads, but checkpointer-stored state from earlier turns
may still carry them in ``fetched_refs``. If the synthesizer cites one,
the verifier must treat it as unresolved — never as a successful EHR
document citation — so a stale synthetic id cannot pass the citation
gate.
"""

from __future__ import annotations

from copilot.graph import _scrub_unresolvable_refs


def test_scrub_synthetic_doc_refs_removes_openemr_upload_prefix() -> None:
    fetched = {
        "DocumentReference/openemr-upload-deadbeef",
        "DocumentReference/real-42",
        "Observation/obs-1",
        "guideline:abc-123",
    }
    scrubbed = _scrub_unresolvable_refs(fetched)
    assert "DocumentReference/openemr-upload-deadbeef" not in scrubbed
    assert scrubbed == {
        "DocumentReference/real-42",
        "Observation/obs-1",
        "guideline:abc-123",
    }


def test_scrub_synthetic_doc_refs_preserves_non_synthetic_refs() -> None:
    fetched = {
        "DocumentReference/lab-001",
        "DocumentReference/upload-2",  # 'upload-' alone is not synthetic
    }
    assert _scrub_unresolvable_refs(fetched) == fetched


def test_scrub_synthetic_doc_refs_empty_input() -> None:
    assert _scrub_unresolvable_refs(set()) == set()


def test_scrub_unresolvable_refs_removes_query_shaped_fhir_refs() -> None:
    fetched = {
        "Observation/_summary=count?patient=fixture-3",
        "Observation/obs-bp-1",
        "Encounter/_search?patient=fixture-1",
        "DocumentReference/doc-1",
    }

    scrubbed = _scrub_unresolvable_refs(fetched)

    assert scrubbed == {
        "Observation/obs-bp-1",
        "DocumentReference/doc-1",
    }
