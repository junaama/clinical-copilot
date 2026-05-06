"""Tests for ``copilot.supervisor.workers`` (issue 009).

Workers narrow the tool surface ``create_agent`` sees, so the LLM
cannot pick across capability boundaries during a structured workflow.
The tool filter is the meat of these tests; the inner agent's behaviour
is tested through the eval harness once issues 006/008 land their tools.

We assert:

* ``WORKER_TOOL_ALLOWLIST`` covers the tools the PRD calls out.
* ``_filter_tools`` returns only allowlisted tools and is silent when
  the underlying issue 006 / 008 tools are not yet registered (which
  reflects the in-progress state of the dependency tree at this commit).
* ``_extract_refs`` finds ``fhir_ref``, ``document_ref``, and
  ``guideline_ref`` JSON keys across a tool-message payload.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from copilot.supervisor.workers import (
    WORKER_TOOL_ALLOWLIST,
    _extract_refs,
    _filter_tools,
)


def _stub_tool(name: str) -> StructuredTool:
    """Build a minimal StructuredTool with a known name."""

    def _fn() -> str:
        return name

    return StructuredTool.from_function(func=_fn, name=name, description="stub")


def test_intake_extractor_allowlist_matches_prd() -> None:
    assert WORKER_TOOL_ALLOWLIST["intake_extractor"] == frozenset(
        {
            "attach_document",
            "list_patient_documents",
            "extract_document",
            "get_patient_demographics",
        }
    )


def test_evidence_retriever_allowlist_matches_prd() -> None:
    assert WORKER_TOOL_ALLOWLIST["evidence_retriever"] == frozenset(
        {
            "retrieve_evidence",
            "get_active_problems",
        }
    )


def test_filter_tools_keeps_only_allowed() -> None:
    tools = [
        _stub_tool("attach_document"),
        _stub_tool("get_patient_demographics"),
        _stub_tool("get_recent_vitals"),  # NOT in allowlist
        _stub_tool("resolve_patient"),  # NOT in allowlist
    ]
    filtered = _filter_tools(tools, WORKER_TOOL_ALLOWLIST["intake_extractor"])
    names = {t.name for t in filtered}
    assert names == {"attach_document", "get_patient_demographics"}


def test_filter_tools_tolerates_missing_tools() -> None:
    """Until issues 006 / 008 land their tools, the worker filter must
    not blow up — it returns an empty list when none of the allowlisted
    tools are registered yet.
    """
    tools = [
        _stub_tool("get_recent_vitals"),
        _stub_tool("resolve_patient"),
    ]
    filtered = _filter_tools(tools, WORKER_TOOL_ALLOWLIST["intake_extractor"])
    assert filtered == []


def test_extract_refs_finds_fhir_refs() -> None:
    msg = ToolMessage(
        content='{"ok": true, "rows": [{"fhir_ref": "Patient/abc"}, {"fhir_ref": "Observation/1"}]}',
        tool_call_id="t1",
    )
    refs = _extract_refs(msg)
    assert refs == ["Patient/abc", "Observation/1"]


def test_extract_refs_finds_document_refs() -> None:
    msg = ToolMessage(
        content='{"ok": true, "document_ref": "DocumentReference/lab-1"}',
        tool_call_id="t2",
    )
    refs = _extract_refs(msg)
    assert refs == ["DocumentReference/lab-1"]


def test_extract_refs_finds_guideline_refs() -> None:
    msg = ToolMessage(
        content='{"ok": true, "chunks": [{"guideline_ref": "guideline:abc-123"}]}',
        tool_call_id="t3",
    )
    refs = _extract_refs(msg)
    assert refs == ["guideline:abc-123"]


def test_extract_refs_combines_all_three_types() -> None:
    msg = ToolMessage(
        content=(
            '{"fhir_ref": "Patient/p1"} '
            '{"document_ref": "DocumentReference/d1"} '
            '{"guideline_ref": "guideline:g1"}'
        ),
        tool_call_id="t4",
    )
    refs = _extract_refs(msg)
    assert set(refs) == {"Patient/p1", "DocumentReference/d1", "guideline:g1"}
