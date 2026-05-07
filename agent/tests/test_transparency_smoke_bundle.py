"""Transparency smoke bundle (issue 048).

Single-file end-to-end smoke that exercises every transparency surface
introduced by issues 040-047. The per-issue tests pin individual pieces
of the contract (``test_chart_citations.py``, ``test_guideline_failure_ui.py``,
``test_panel_triage_failure_ui.py``, ``test_conversation_rehydration.py``,
etc.); this bundle re-exercises each AC against the wire shape so the
"submission ready" smoke can be a single ``pytest -k transparency_smoke``
invocation instead of a hunt across nine files.

Maps each AC of issue 048 to a single backend smoke case:

* AC1 — chart brief / chart answer with visible route metadata
        (``test_smoke_ac1_chart_route_metadata_on_chart_answer``)
* AC2 — medication follow-up with visible chart source chips
        (``test_smoke_ac2_medication_followup_renders_chart_chips``)
* AC3 — guideline retrieval-failure case fails closed
        (``test_smoke_ac3_guideline_retrieval_failure_route_is_refusal``)
* AC4 — panel triage success or safe failure state
        (``test_smoke_ac4_panel_triage_success_route``,
         ``test_smoke_ac4_panel_triage_failure_safe_state``)
* AC7 — conversation rehydration preserves provenance
        (``test_smoke_ac7_rehydration_preserves_block_route_diagnostics``)
* AC8 — document source chips when document evidence available
        (``test_smoke_ac8_document_source_chips_render_with_filename_page``)
* AC10 — no raw chart-content leakage in the diagnostics envelope
        (``test_smoke_ac10_diagnostics_envelope_does_not_leak_chart_content``)

ACs 5, 6, 9 are UI-only — covered by the frontend smoke bundle in
``copilot-ui/src/__tests__/transparencySmokeBundle.test.tsx`` and the
operator runbook (``runbook/003-transparency-smoke-bundle.md``).

Pattern follows ``test_chat_contract.py`` (StubGraph + TestClient) and
``test_conversation_rehydration.py`` (in-memory turn registry). No LLM,
no SMART, no Postgres — fixture mode only.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from copilot.api.schemas import (
    Citation,
    OvernightBlock,
    PlainBlock,
    TriageBlock,
    derive_route_metadata,
)
from copilot.conversation_turns import (
    ConversationTurnRegistry,
    InMemoryTurnStore,
)
from copilot.conversations import (
    ConversationRegistry,
    InMemoryConversationStore,
)
from copilot.session import (
    InMemorySessionStore,
    SessionGateway,
    SessionRow,
)

# ---------------------------------------------------------------------------
# Stub graphs — each maps to one transparency surface.
# ---------------------------------------------------------------------------


class _ChartAnswerGraph:
    """W-2 chart answer with a vitals citation. Covers AC1."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        block = OvernightBlock(
            lead="Eduardo had a quiet night with one transient hypotensive event.",
            deltas=({"label": "BP", "from": "138/82", "to": "90/60", "dir": "down"},),
            timeline=(
                {
                    "t": "03:14",
                    "kind": "Vital",
                    "text": "BP 90/60",
                    "fhir_ref": "Observation/obs-bp-2",
                },
            ),
            citations=(
                Citation(
                    card="vitals",
                    label="BP 90/60 · 03:14",
                    fhir_ref="Observation/obs-bp-2",
                ),
            ),
            followups=("Show full overnight timeline",),
        )
        return {
            "messages": [AIMessage(content=block.lead)],
            "patient_id": inputs.get("patient_id"),
            "workflow_id": "W-2",
            "classifier_confidence": 0.91,
            "decision": "allow",
            "block": block.model_dump(by_alias=True),
        }


class _MedicationFollowupGraph:
    """W-2 medication follow-up with chart medication chips. Covers AC2."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        block = PlainBlock(
            lead="Active orders include metformin 500 mg PO BID and lisinopril 10 mg daily.",
            citations=(
                Citation(
                    card="medications",
                    label="metformin 500 mg PO BID",
                    fhir_ref="MedicationRequest/m1",
                ),
                Citation(
                    card="medications",
                    label="lisinopril 10 mg daily",
                    fhir_ref="MedicationRequest/m2",
                ),
            ),
        )
        return {
            "messages": [AIMessage(content=block.lead)],
            "patient_id": inputs.get("patient_id"),
            "workflow_id": "W-2",
            "classifier_confidence": 0.95,
            "decision": "allow",
            "block": block.model_dump(by_alias=True),
        }


class _GuidelineFailureGraph:
    """W-EVD retrieval-failure refusal. Covers AC3."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        # The verifier (issue 041) replaces any synthesized clinical claim
        # with corpus-bound copy when the retrieval tool fails. We pin the
        # post-verifier shape here.
        block = PlainBlock(
            lead=(
                "I could not retrieve guideline evidence for that question, "
                "so I cannot give a corpus-bound answer right now."
            )
        )
        return {
            "messages": [AIMessage(content=block.lead)],
            "patient_id": inputs.get("patient_id"),
            "workflow_id": "W-EVD",
            "classifier_confidence": 0.88,
            "decision": "refused_unsourced",
            "supervisor_action": "retrieve_evidence",
            "block": block.model_dump(by_alias=True),
        }


class _PanelTriageSuccessGraph:
    """W-1 panel triage success. Covers AC4 (success path)."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        block = TriageBlock(
            lead="3 of 5 patients have something new since 22:00.",
            cohort=(
                {
                    "id": "fixture-1",
                    "name": "Eduardo Perez",
                    "age": 68,
                    "room": "MS-412",
                    "score": 86,
                    "trend": "up",
                    "reasons": ["NEWS2 +3 since 22:00"],
                    "self": True,
                    "fhir_ref": "Patient/fixture-1",
                },
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
            "decision": "allow",
            "block": block.model_dump(by_alias=True),
        }


class _PanelTriageFailureGraph:
    """W-1 panel triage tool_failure → ``Panel data unavailable``. AC4 (safe failure)."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        block = PlainBlock(
            lead=(
                "Panel data is unavailable right now, so I can't rank "
                "the patients on your panel."
            )
        )
        return {
            "messages": [AIMessage(content=block.lead)],
            "patient_id": inputs.get("patient_id"),
            "workflow_id": "W-1",
            "classifier_confidence": 0.92,
            "decision": "tool_failure",
            "supervisor_action": "",
            "block": block.model_dump(by_alias=True),
        }


class _DocumentChipGraph:
    """W-DOC document-grounded answer with a filename · page chip. AC8."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        block = PlainBlock(
            lead="The lab report shows LDL 180 mg/dL.",
            citations=(
                Citation(
                    card="documents",
                    label="lab_results.pdf · page 1",
                    fhir_ref="DocumentReference/d1",
                ),
            ),
        )
        return {
            "messages": [AIMessage(content=block.lead)],
            "patient_id": inputs.get("patient_id"),
            "workflow_id": "W-DOC",
            "classifier_confidence": 0.94,
            "decision": "allow",
            "block": block.model_dump(by_alias=True),
        }


# ---------------------------------------------------------------------------
# Shared TestClient fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
def smoke_client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with in-memory session, conversation, and turn registries.

    The graph stub is set per-test on ``app.state.graph`` so each AC can
    plug in its own deterministic response shape.
    """
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from copilot import server as server_mod

    @asynccontextmanager
    async def _stub_checkpointer(_settings):
        yield None

    monkeypatch.setattr(
        server_mod, "build_graph", lambda *_a, **_kw: _ChartAnswerGraph()
    )
    monkeypatch.setattr(server_mod, "open_checkpointer", _stub_checkpointer)

    session_gateway = SessionGateway(store=InMemorySessionStore())
    conversation_registry = ConversationRegistry(store=InMemoryConversationStore())
    turn_registry = ConversationTurnRegistry(store=InMemoryTurnStore())

    with TestClient(server_mod.app) as client:
        server_mod.app.state.session_gateway = session_gateway
        server_mod.app.state.conversation_registry = conversation_registry
        server_mod.app.state.conversation_turn_registry = turn_registry
        server_mod.app.state.title_summarizer = None
        server_mod.app.state.graph = _ChartAnswerGraph()
        yield client


async def _seed_session(
    fhir_user: str = "Practitioner/practitioner-dr-smith",
    *,
    display_name: str = "Dr. Smith",
) -> str:
    from copilot import server as server_mod

    gateway = server_mod.app.state.session_gateway
    sid = f"sess-{fhir_user.replace('/', '-')}"
    now = time.time()
    await gateway.create_session(
        SessionRow(
            session_id=sid,
            oe_user_id=42,
            display_name=display_name,
            fhir_user=fhir_user,
            created_at=now,
            expires_at=now + 3600,
        )
    )
    return sid


def _new_conversation(client: TestClient, cookie: str) -> str:
    return client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]


# ---------------------------------------------------------------------------
# AC1 — chart brief / chart answer with visible route metadata.
# ---------------------------------------------------------------------------


async def test_smoke_ac1_chart_route_metadata_on_chart_answer(
    smoke_client: TestClient,
) -> None:
    """A chart-route turn surfaces ``state.route.kind = "chart"`` with a
    non-empty user-facing label, and the block carries the chart citation
    that grounds the lead."""
    from copilot import server as server_mod

    server_mod.app.state.graph = _ChartAnswerGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    response = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "fixture-1",
            "message": "What happened to Eduardo overnight?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    route = body["state"]["route"]
    assert route["kind"] == "chart"
    assert isinstance(route["label"], str) and route["label"]
    # Backend owns the copy — the panel-failure label must not leak here.
    assert route["label"] != "Panel data unavailable"

    # Chart claim is grounded by an Observation chip.
    block = body["block"]
    assert block["kind"] == "overnight"
    assert any(
        c["fhir_ref"] == "Observation/obs-bp-2" and c["card"] == "vitals"
        for c in block.get("citations", [])
    )


# ---------------------------------------------------------------------------
# AC2 — medication follow-up with visible chart source chips.
# ---------------------------------------------------------------------------


async def test_smoke_ac2_medication_followup_renders_chart_chips(
    smoke_client: TestClient,
) -> None:
    """A medication follow-up turn renders source chips on the
    ``medications`` card with human-readable labels (not the opaque
    ``MedicationRequest/<id>`` resource handle)."""
    from copilot import server as server_mod

    server_mod.app.state.graph = _MedicationFollowupGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    response = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "fixture-1",
            "message": "What active medications is Eduardo on?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    block = body["block"]
    citations = block.get("citations", [])
    assert len(citations) == 2

    cards = {c["card"] for c in citations}
    assert cards == {"medications"}

    labels = [c["label"] for c in citations]
    assert any("metformin" in label.lower() for label in labels)
    assert any("lisinopril" in label.lower() for label in labels)
    # No opaque resource handles in the chip text.
    for label in labels:
        assert "MedicationRequest/" not in label

    # Route still says chart for a chart-grounded medication answer.
    assert body["state"]["route"]["kind"] == "chart"


# ---------------------------------------------------------------------------
# AC3 — guideline no-evidence / retrieval-failure case fails closed.
# ---------------------------------------------------------------------------


async def test_smoke_ac3_guideline_retrieval_failure_route_is_refusal(
    smoke_client: TestClient,
) -> None:
    """A W-EVD retrieval failure surfaces as ``kind: refusal`` (not
    ``guideline``), the block lead is corpus-bound copy, and the
    diagnostics envelope records ``decision = refused_unsourced`` so
    the technical-details affordance can confirm it failed closed."""
    from copilot import server as server_mod

    server_mod.app.state.graph = _GuidelineFailureGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    response = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "",
            "message": "What does ADA say about A1c?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Route = refusal (not guideline) — the most damaging mislabel would
    # be advertising the answer as a successful guideline retrieval.
    route = body["state"]["route"]
    assert route["kind"] == "refusal"
    assert route["kind"] != "guideline"
    assert route["kind"] != "chart"

    # Diagnostics carry the failed-closed decision off-stage.
    diagnostics = body["state"]["diagnostics"]
    assert diagnostics["decision"] == "refused_unsourced"
    assert diagnostics["supervisor_action"] == "retrieve_evidence"

    # Block has no fabricated citations — refusal-closed turns must not
    # claim corpus evidence.
    block = body["block"]
    assert block.get("citations", []) == []
    # Corpus-bound copy must not include internal failure tokens.
    for marker in ("retrieval_failed", "no_active_user", "tool_failure"):
        assert marker not in body["reply"].lower()


# ---------------------------------------------------------------------------
# AC4 — panel triage success or safe failure state.
# ---------------------------------------------------------------------------


async def test_smoke_ac4_panel_triage_success_route(
    smoke_client: TestClient,
) -> None:
    """A successful W-1 turn renders a triage block + ``Reviewing your panel`` route."""
    from copilot import server as server_mod

    server_mod.app.state.graph = _PanelTriageSuccessGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    response = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "",
            "message": "Who needs attention first?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    route = body["state"]["route"]
    assert route["kind"] == "panel"
    assert route["label"] == "Reviewing your panel"

    block = body["block"]
    assert block["kind"] == "triage"
    assert len(block["cohort"]) >= 1


async def test_smoke_ac4_panel_triage_failure_safe_state(
    smoke_client: TestClient,
) -> None:
    """A panel triage failure surfaces ``kind: panel`` with a
    ``Panel data unavailable`` label and a safe plain-block lead — no
    fabricated cohort, no internal leak markers, no clinical-looking
    ranking."""
    from copilot import server as server_mod

    server_mod.app.state.graph = _PanelTriageFailureGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    response = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "",
            "message": "Who needs attention first?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    route = body["state"]["route"]
    assert route["kind"] == "panel"
    assert route["label"] == "Panel data unavailable"

    block = body["block"]
    assert block["kind"] == "plain"
    assert "panel" in block["lead"].lower() and "unavailable" in block["lead"].lower()

    # No internal-leak markers in the user-visible reply.
    reply = body["reply"].lower()
    for marker in (
        "careteam_denied",
        "denied_authz",
        "run_panel_triage",
        "tool_failure",
        "http_401",
        "http_403",
    ):
        assert marker not in reply

    # Diagnostics still record the decision off-stage so a developer can confirm
    # the system failed closed rather than hallucinated.
    diagnostics = body["state"]["diagnostics"]
    assert diagnostics["decision"] == "tool_failure"


# ---------------------------------------------------------------------------
# AC7 — conversation rehydration preserves provenance.
# ---------------------------------------------------------------------------


async def test_smoke_ac7_rehydration_preserves_block_route_diagnostics(
    smoke_client: TestClient,
) -> None:
    """A chat turn's structured block + route + diagnostics round-trip
    through the messages endpoint so the sidebar reopen renders the same
    surface the clinician saw the first time."""
    from copilot import server as server_mod

    server_mod.app.state.graph = _ChartAnswerGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    chat_resp = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "fixture-1",
            "message": "What happened to Eduardo overnight?",
        },
    )
    assert chat_resp.status_code == 200

    msgs_resp = smoke_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": cookie},
    )
    assert msgs_resp.status_code == 200
    body = msgs_resp.json()

    assert len(body["messages"]) == 2
    agent_msg = body["messages"][1]
    assert agent_msg["role"] == "agent"

    # Block kind is preserved (overnight, not flattened to plain).
    assert agent_msg["block"]["kind"] == "overnight"
    # Citation chip rehydrates with its fhir_ref.
    assert any(
        c["fhir_ref"] == "Observation/obs-bp-2"
        for c in agent_msg["block"].get("citations", [])
    )
    # Route metadata round-trips for the badge.
    assert agent_msg["route"]["kind"] == "chart"
    assert agent_msg["route"]["label"] == "Reading the patient record"
    # Diagnostics envelope round-trips for the technical-details affordance.
    assert agent_msg["diagnostics"]["decision"] == "allow"
    assert agent_msg["workflow_id"] == "W-2"


# ---------------------------------------------------------------------------
# AC8 — document source chips when document evidence is available.
# ---------------------------------------------------------------------------


async def test_smoke_ac8_document_source_chips_render_with_filename_page(
    smoke_client: TestClient,
) -> None:
    """A W-DOC turn carries a ``documents`` chip whose label reads
    ``<filename> · page <n>`` — never the opaque resource handle."""
    from copilot import server as server_mod

    server_mod.app.state.graph = _DocumentChipGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    response = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "fixture-1",
            "message": "What did the lab report say about LDL?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    block = body["block"]
    citations = block.get("citations", [])
    assert len(citations) == 1
    chip = citations[0]
    assert chip["card"] == "documents"
    assert "lab_results.pdf" in chip["label"]
    assert "page 1" in chip["label"].lower()
    # Defense-in-depth: the chip label must not be the opaque resource handle.
    assert chip["label"] != "DocumentReference/d1"
    assert "DocumentReference (documents)" not in chip["label"]


# ---------------------------------------------------------------------------
# AC10 — no raw chart-content leakage in the diagnostics envelope.
# ---------------------------------------------------------------------------


async def test_smoke_ac10_diagnostics_envelope_does_not_leak_chart_content(
    smoke_client: TestClient,
) -> None:
    """The diagnostics envelope (``state.diagnostics``) is for technical
    metadata only (decision, supervisor_action). It must NEVER carry raw
    chart values, patient names, MRNs, or arbitrary tool-output payloads
    — those go in the structured block's citation chips, where their
    visibility is part of the audit contract.

    This is the smoke-side guard for AC10 ("smoke instructions or tests
    avoid raw chart-content leakage in logs"): if a future change tries
    to widen ``diagnostics`` to a free-form bag, this test will catch it.
    """
    from copilot import server as server_mod

    server_mod.app.state.graph = _ChartAnswerGraph()

    cookie = await _seed_session()
    new_id = _new_conversation(smoke_client, cookie)

    response = smoke_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "patient_id": "fixture-1",
            "message": "What happened to Eduardo overnight?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    diagnostics = body["state"]["diagnostics"]
    # Closed-set keys only — anything else risks leaking chart content.
    assert set(diagnostics.keys()) == {"decision", "supervisor_action"}
    # The values must be small string identifiers, not arbitrary payloads.
    assert isinstance(diagnostics["decision"], str)
    assert isinstance(diagnostics["supervisor_action"], str)
    # Defense in depth: a clinical value (BP) from the chart citation must
    # not have leaked into the diagnostics text.
    for value in diagnostics.values():
        assert "90/60" not in value
        assert "Eduardo" not in value


# ---------------------------------------------------------------------------
# Route-derivation matrix coverage — pin every route the smoke depends on.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("workflow_id", "decision", "supervisor_action", "expected_kind", "expected_label"),
    [
        ("W-2", "allow", None, "chart", "Reading the patient record"),
        ("W-1", "allow", None, "panel", "Reviewing your panel"),
        ("W-1", "tool_failure", None, "panel", "Panel data unavailable"),
        ("W-1", "refused_unsourced", None, "panel", "Panel data unavailable"),
        ("W-EVD", "allow", "retrieve_evidence", "guideline", "Searching guideline evidence"),
        ("W-EVD", "refused_unsourced", "retrieve_evidence", "refusal", "Cannot ground this answer"),
        ("W-DOC", "allow", None, "document", "Reading the uploaded document"),
        ("W-DOC", "refused_unsourced", None, "refusal", "Cannot ground this answer"),
    ],
)
def test_smoke_route_metadata_covers_every_smoke_path(
    workflow_id: str,
    decision: str,
    supervisor_action: str | None,
    expected_kind: str,
    expected_label: str,
) -> None:
    """Pin the route-kind / label mapping for every transparency surface
    the smoke bundle exercises. A drift in ``derive_route_metadata`` would
    silently break the route badge across all of them; this matrix is the
    stable contract."""
    route = derive_route_metadata(
        workflow_id=workflow_id,
        decision=decision,
        supervisor_action=supervisor_action,
    )
    assert route.kind == expected_kind
    assert route.label == expected_label
