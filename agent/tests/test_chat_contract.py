"""Tests for the chat-API wire contract.

Asserts that:
* every block variant in :mod:`copilot.api.schemas` validates round-trip
* the discriminated union routes correctly on ``kind``
* the citation-card mapper covers the documented FHIR resource types
* :func:`POST /chat` returns a contract-shaped response in fixture mode

These tests do NOT exercise the LLM. The /chat test monkey-patches the
graph's ``ainvoke`` to return a deterministic state so we test the
serialization edges, not Anthropic API behavior.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from copilot.api.schemas import (
    Block,
    ChatRequest,
    ChatResponse,
    Citation,
    CohortPatient,
    OvernightBlock,
    PlainBlock,
    RouteMetadata,
    TriageBlock,
    derive_route_metadata,
    fhir_ref_to_card,
)

# ---------------------------------------------------------------------------
# Schema round-trips
# ---------------------------------------------------------------------------


def test_triage_block_validates() -> None:
    """A hand-built TriageBlock dict round-trips through Pydantic."""

    payload: dict[str, Any] = {
        "kind": "triage",
        "lead": "3 of 5 patients have something new since 22:00.",
        "cohort": [
            {
                "id": "p1",
                "name": "Wade235 Bednar518",
                "age": 33,
                "room": "MS-412",
                "score": 86,
                "trend": "up",
                "reasons": ["NEWS2 +3", "WBC 14.8"],
                "self": True,
                "fhir_ref": "Patient/4",
            }
        ],
        "citations": [
            {
                "card": "vitals",
                "label": "Vitals · last 4",
                "fhir_ref": "Observation/obs-1",
            }
        ],
        "followups": ["Draft an SBAR for Wade235"],
    }
    block = TriageBlock.model_validate(payload)
    assert block.kind == "triage"
    assert len(block.cohort) == 1
    assert block.cohort[0].is_self is True
    serialized = block.model_dump(by_alias=True)
    assert serialized["cohort"][0]["self"] is True


def test_overnight_block_validates() -> None:
    """An OvernightBlock dict round-trips, including the ``from`` alias."""

    payload: dict[str, Any] = {
        "kind": "overnight",
        "lead": "Hypotensive event at 03:14 with full recovery.",
        "deltas": [
            {"label": "BP", "from": "138/82", "to": "90/60", "dir": "down"},
        ],
        "timeline": [
            {
                "t": "03:14",
                "kind": "Vital",
                "text": "BP 90/60",
                "fhir_ref": "Observation/obs-bp-2",
            }
        ],
        "citations": [
            {
                "card": "vitals",
                "label": "Vitals",
                "fhir_ref": "Observation/obs-bp-2",
            }
        ],
        "followups": ["Suggest next orders"],
    }
    block = OvernightBlock.model_validate(payload)
    assert block.deltas[0].from_ == "138/82"
    serialized = block.model_dump(by_alias=True)
    assert serialized["deltas"][0]["from"] == "138/82"


def test_plain_block_validates() -> None:
    """A PlainBlock with no citations or followups validates."""

    block = PlainBlock.model_validate({"kind": "plain", "lead": "What did you mean?"})
    assert block.lead == "What did you mean?"
    assert block.citations == ()
    assert block.followups == ()


def test_block_discriminator_routes() -> None:
    """Pydantic's discriminated union dispatches on ``kind``."""

    adapter: TypeAdapter[Block] = TypeAdapter(Block)
    triage = adapter.validate_python({"kind": "triage", "lead": "x", "cohort": []})
    overnight = adapter.validate_python(
        {"kind": "overnight", "lead": "x", "deltas": [], "timeline": []}
    )
    plain = adapter.validate_python({"kind": "plain", "lead": "x"})
    assert isinstance(triage, TriageBlock)
    assert isinstance(overnight, OvernightBlock)
    assert isinstance(plain, PlainBlock)


def test_unknown_block_kind_rejected() -> None:
    """The discriminator rejects unknown kinds rather than coerce."""

    adapter: TypeAdapter[Block] = TypeAdapter(Block)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "mystery", "lead": "x"})


# ---------------------------------------------------------------------------
# Citation card mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fhir_ref", "category", "expected"),
    [
        ("Observation/obs-1", "vital-signs", "vitals"),
        ("Observation/obs-1", "laboratory", "labs"),
        ("MedicationRequest/m1", None, "medications"),
        ("MedicationAdministration/ma1", None, "medications"),
        ("Condition/c1", None, "problems"),
        ("AllergyIntolerance/a1", None, "allergies"),
        ("DocumentReference/d1", None, "documents"),
        ("Encounter/e1", None, "encounters"),
        ("DiagnosticReport/r1", None, "labs"),
        ("ServiceRequest/s1", None, "prescriptions"),
        ("Observation/obs-1", None, "other"),
        ("UnknownResource/x", None, "other"),
        (None, None, "other"),
    ],
)
def test_citation_card_mapping(
    fhir_ref: str | None, category: str | None, expected: str
) -> None:
    """Every documented FHIR-resource → card mapping is covered."""

    assert fhir_ref_to_card(fhir_ref, observation_category=category) == expected


# ---------------------------------------------------------------------------
# Strip cite tags
# ---------------------------------------------------------------------------


def test_strip_cite_tags_cleans_lead() -> None:
    """Block synthesizers must produce clean prose, not <cite/> tags."""

    from copilot.blocks import strip_cite_tags

    text = (
        'BP fell to 90/60 at 03:14 <cite ref="Observation/obs-bp-2"/> and '
        'recovered <cite ref="Observation/obs-bp-3"/>.'
    )
    cleaned = strip_cite_tags(text)
    assert "<cite" not in cleaned
    assert "BP fell to 90/60" in cleaned


def test_build_citations_drops_unfetched_refs() -> None:
    """Cited refs not in ``fetched_refs`` are dropped — defense in depth."""

    from copilot.blocks import build_citations

    citations = build_citations(
        cited_refs=["Observation/real", "Observation/hallucinated"],
        fetched_refs=["Observation/real"],
        observation_categories={"Observation/real": "vital-signs"},
    )
    assert len(citations) == 1
    assert citations[0].card == "vitals"
    assert citations[0].fhir_ref == "Observation/real"


# ---------------------------------------------------------------------------
# /chat endpoint contract (no LLM, no SMART)
# ---------------------------------------------------------------------------


class _StubGraph:
    """Deterministic graph stub for contract tests."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        block = TriageBlock(
            lead="3 of 5 patients have something new since 22:00.",
            cohort=(
                CohortPatient(
                    id="fixture-1",
                    name="Eduardo Perez",
                    age=68,
                    room="MS-412",
                    score=86,
                    trend="up",
                    reasons=("NEWS2 +3 since 22:00",),
                    is_self=True,
                    fhir_ref="Patient/fixture-1",
                ),
            ),
            citations=(
                Citation(
                    card="vitals",
                    label="Vitals · last 4",
                    fhir_ref="Observation/obs-bp-2",
                ),
            ),
            followups=("Draft an SBAR for Eduardo Perez",),
        )
        return {
            "messages": [AIMessage(content=block.lead)],
            "patient_id": inputs.get("patient_id"),
            "workflow_id": "W-1",
            "classifier_confidence": 0.93,
            "block": block.model_dump(by_alias=True),
        }


@pytest.fixture
def fixture_client(monkeypatch: pytest.MonkeyPatch):
    """Build a TestClient that does not exercise the LLM.

    We replace ``build_graph`` and ``open_checkpointer`` with no-ops so the
    server's lifespan can run safely in fixture mode without an Anthropic key
    or a Postgres DSN. The graph is then overridden with a deterministic
    stub before each request.
    """

    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from contextlib import asynccontextmanager

    from copilot import server

    @asynccontextmanager
    async def _stub_open_checkpointer(_settings):
        yield None

    monkeypatch.setattr(server, "build_graph", lambda *_a, **_kw: _StubGraph())
    monkeypatch.setattr(server, "open_checkpointer", _stub_open_checkpointer)

    with TestClient(server.app) as client:
        # Ensure lifespan-installed graph is the stub even if reuse swapped it.
        server.app.state.graph = _StubGraph()
        yield client


def test_chat_response_contains_block(fixture_client: TestClient) -> None:
    """POST /chat returns a contract-shaped ChatResponse."""

    response = fixture_client.post(
        "/chat",
        json={
            "conversation_id": "demo-1",
            "patient_id": "fixture-1",
            "user_id": "naama",
            "smart_access_token": "stub",
            "message": "Who needs attention first?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation_id"] == "demo-1"
    assert isinstance(body["reply"], str) and body["reply"]
    assert body["block"]["kind"] in {"triage", "overnight", "plain"}
    # Issue 030: state.cache_hits is always present (empty list when no
    # cache hit fired this turn). The deployed e2e test (issue 030)
    # asserts a non-empty list on the second post-upload chat turn.
    assert body["state"]["cache_hits"] == []
    # ChatResponse re-validates as a sanity check.
    parsed = ChatResponse.model_validate(body)
    assert parsed.block.lead == body["reply"]


def test_chat_response_surfaces_cache_hits_from_state(
    fixture_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the graph emits ``cache_hits``, the /chat response carries
    them through under ``state.cache_hits`` so a client can prove the
    extraction was cache-served (issue 030)."""

    from copilot import server

    class _CacheHitGraph:
        async def ainvoke(
            self, inputs: dict[str, Any], config: dict[str, Any]
        ) -> dict[str, Any]:
            from langchain_core.messages import AIMessage

            block = PlainBlock(lead="cache-served reply")
            return {
                "messages": [AIMessage(content="cache-served reply")],
                "patient_id": inputs.get("patient_id"),
                "workflow_id": "W-DOC",
                "classifier_confidence": 0.91,
                "block": block.model_dump(by_alias=True),
                "cache_hits": ["document_id:abc-42"],
            }

    monkeypatch.setattr(server.app.state, "graph", _CacheHitGraph())

    response = fixture_client.post(
        "/chat",
        json={
            "conversation_id": "demo-cache",
            "patient_id": "fixture-1",
            "user_id": "naama",
            "smart_access_token": "stub",
            "message": "remind me of the LDL on the same upload",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"]["cache_hits"] == ["document_id:abc-42"]


def test_chat_request_accepts_minimal_body() -> None:
    """ChatRequest only requires conversation_id and message."""

    req = ChatRequest.model_validate(
        {"conversation_id": "demo-1", "message": "hi"}
    )
    assert req.patient_id == ""
    assert req.smart_access_token == ""


def test_chat_request_rejects_empty_conversation_id() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"conversation_id": "", "message": "hi"})


# ---------------------------------------------------------------------------
# Route metadata (issue 039)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("workflow_id", "decision", "supervisor_action", "expected_kind"),
    [
        # Chart happy path — W-2 single-patient brief, no decision short-circuit.
        ("W-2", "allow", None, "chart"),
        # Chart fallback for advisory workflow_id values that aren't W-1/W-DOC/W-EVD.
        ("W-7", "allow", None, "chart"),
        ("unclear", "allow", None, "chart"),
        # Panel triage.
        ("W-1", "allow", None, "panel"),
        # Guideline / evidence path — workflow_id wins.
        ("W-EVD", "allow", None, "guideline"),
        # Guideline path triggered through the supervisor's action when the
        # classifier didn't surface W-EVD on the latest turn.
        ("W-2", "allow", "retrieve_evidence", "guideline"),
        # Document upload path.
        ("W-DOC", "allow", None, "document"),
        ("W-2", "allow", "extract", "document"),
        # Decision short-circuits override the workflow id.
        ("W-1", "clarify", None, "clarify"),
        ("W-EVD", "refused_unsourced", None, "refusal"),
        ("W-DOC", "refused_unsourced", None, "refusal"),
        ("W-2", "tool_failure", None, "refusal"),
    ],
)
def test_derive_route_metadata_maps_state_to_route(
    workflow_id: str | None,
    decision: str | None,
    supervisor_action: str | None,
    expected_kind: str,
) -> None:
    """``derive_route_metadata`` covers the full route-kind matrix."""

    route = derive_route_metadata(
        workflow_id=workflow_id,
        decision=decision,
        supervisor_action=supervisor_action,
    )
    assert route.kind == expected_kind
    assert route.label  # never empty


def test_route_metadata_rejects_unknown_kind() -> None:
    """``RouteMetadata`` is closed-set so the wire can't drift open."""

    with pytest.raises(ValidationError):
        RouteMetadata.model_validate({"kind": "mystery", "label": "x"})


def test_route_metadata_rejects_empty_label() -> None:
    with pytest.raises(ValidationError):
        RouteMetadata.model_validate({"kind": "chart", "label": ""})


def test_chat_response_carries_chart_route_for_chart_answer(
    fixture_client: TestClient,
) -> None:
    """The chart-route happy path renders a structured route on /chat.

    The stub graph emits ``W-2`` with no supervisor / refusal short-circuit,
    so the response must surface ``state.route`` with ``kind: "chart"`` and
    a non-empty user-facing label.
    """

    from copilot import server

    class _ChartGraph:
        async def ainvoke(
            self, inputs: dict[str, Any], config: dict[str, Any]
        ) -> dict[str, Any]:
            from langchain_core.messages import AIMessage

            block = PlainBlock(lead="Eduardo had a quiet night.")
            return {
                "messages": [AIMessage(content=block.lead)],
                "patient_id": inputs.get("patient_id"),
                "workflow_id": "W-2",
                "classifier_confidence": 0.91,
                "decision": "allow",
                "block": block.model_dump(by_alias=True),
            }

    server.app.state.graph = _ChartGraph()

    response = fixture_client.post(
        "/chat",
        json={
            "conversation_id": "demo-route-chart",
            "patient_id": "fixture-1",
            "user_id": "naama",
            "smart_access_token": "stub",
            "message": "What happened to Eduardo overnight?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    route = body["state"]["route"]
    assert route["kind"] == "chart"
    assert isinstance(route["label"], str) and len(route["label"]) > 0
    assert route["label"] != "Reading this patient's record"  # backend owns the copy


def test_chat_response_route_reflects_panel_workflow(
    fixture_client: TestClient,
) -> None:
    """The original triage stub maps W-1 to a panel route, not chart."""

    response = fixture_client.post(
        "/chat",
        json={
            "conversation_id": "demo-route-panel",
            "patient_id": "fixture-1",
            "user_id": "naama",
            "smart_access_token": "stub",
            "message": "Who needs attention first?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    route = body["state"]["route"]
    assert route["kind"] == "panel"
    assert route["label"]
