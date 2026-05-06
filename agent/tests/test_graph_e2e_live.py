"""End-to-end live tests against the real graph + real services.

Distinct from ``test_graph_integration.py`` - that suite stubs the LLM /
``create_agent`` / synthesizer and asserts on graph-wiring invariants in
under a second. This suite makes **real** API calls:

* real OpenAI / Anthropic for the chat model and supervisor LLM
* real Cohere ``embed-english-v3.0`` + ``rerank-english-v3.0``
* real Anthropic Claude vision for VLM extraction
* real Postgres (pgvector + ``document_extractions``)
* real OpenEMR Standard API for document upload

These tests cost ~$0.05-$0.20 per run depending on the case mix and take
20-60 s wall-clock. They are gated behind the ``live`` pytest marker and
the default ``addopts = "-m 'not live'"`` line in ``pyproject.toml`` keeps
them out of the unit / integration / pre-push paths.

To run::

    cd agent
    uv run pytest -m live -v tests/test_graph_e2e_live.py

Required env (read straight from ``.env`` via ``Settings``)::

    OPENAI_API_KEY            # if LLM_PROVIDER=openai
    ANTHROPIC_API_KEY         # for VLM + supervisor / classifier when configured
    COHERE_API_KEY            # for retrieval embed + rerank
    CHECKPOINTER_DSN          # Postgres with pgvector + W2 tables migrated
    OPENEMR_FHIR_TOKEN        # static bearer (or run after a SMART login)
    OPENEMR_FHIR_BASE         # default points at Railway prod
    OPENEMR_BASE_URL          # default points at Railway prod
    COPILOT_ADMIN_USER_IDS    # at least one practitioner uuid for the gate
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from copilot.config import Settings
from copilot.graph import build_graph
from copilot.tools.helpers import (
    set_active_registry,
    set_active_smart_token,
    set_active_user_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DOCS = REPO_ROOT / "example-documents"
CHEN_LIPID_PDF = EXAMPLE_DOCS / "lab-results" / "p01-chen-lipid-panel.pdf"


# ---------------------------------------------------------------------------
# Skip-if-not-configured
# ---------------------------------------------------------------------------


def _missing_core_env() -> list[str]:
    """Vars every live test needs (LLM + retrieval + checkpointer + gate)."""
    missing: list[str] = []
    settings = Settings()
    if not settings.openai_api_key.get_secret_value() and settings.llm_provider == "openai":
        missing.append("OPENAI_API_KEY")
    if not settings.anthropic_api_key.get_secret_value():
        missing.append("ANTHROPIC_API_KEY")
    if not settings.cohere_api_key.get_secret_value():
        missing.append("COHERE_API_KEY")
    if not settings.checkpointer_dsn:
        missing.append("CHECKPOINTER_DSN")
    if not settings.admin_user_ids:
        missing.append("COPILOT_ADMIN_USER_IDS")
    return missing


def _missing_upload_env() -> list[str]:
    """Vars only the upload test needs (static OpenEMR bearer)."""
    missing = _missing_core_env()
    settings = Settings()
    if not settings.openemr_fhir_token.get_secret_value():
        missing.append("OPENEMR_FHIR_TOKEN")
    return missing


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        bool(_missing_core_env()),
        reason=(
            "live e2e requires real credentials; missing: "
            + ", ".join(_missing_core_env() or ["(none)"])
        ),
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_graph_turn(
    *,
    settings: Settings,
    conversation_id: str,
    user_message: str,
    extra_messages: list[Any] | None = None,
) -> dict[str, Any]:
    """Build the real graph and fire one turn. Returns the final state.

    Binds the active SMART token + user id from settings into the
    contextvars so tool calls reach OpenEMR. Real ``CHECKPOINTER_DSN``
    is used; choose a unique ``conversation_id`` per test to keep state
    clean across runs.
    """
    # The agent layer reads SMART context from state, but the tool layer
    # uses contextvars for cross-cutting concerns. Mirror what the FastAPI
    # ``/chat`` handler does at server.py:411-421.
    set_active_smart_token(settings.openemr_fhir_token.get_secret_value() or None)
    practitioner = settings.admin_user_ids[0] if settings.admin_user_ids else None
    set_active_user_id(practitioner)
    set_active_registry({})

    graph = build_graph(settings)
    inputs: dict[str, Any] = {
        "messages": [*(extra_messages or []), HumanMessage(content=user_message)],
        "conversation_id": conversation_id,
        "user_id": practitioner,
        "smart_access_token": settings.openemr_fhir_token.get_secret_value() or "",
    }
    config = {"configurable": {"thread_id": conversation_id}}
    return await graph.ainvoke(inputs, config=config)


async def _document_extraction_count(
    *,
    dsn: str,
    patient_id: str,
    document_id: str,
) -> int:
    """Count persisted extraction attempts for the live cache assertion."""
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError:
        pytest.skip("live cache assertion requires psycopg_pool")

    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=2)
    await pool.open()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT count(*)
                    FROM document_extractions
                    WHERE patient_id = %s AND document_id = %s
                    """,
                    (patient_id, document_id),
                )
                row = await cur.fetchone()
                return int(row[0]) if row else 0
    finally:
        await pool.close()


def _last_ai_text(state: dict[str, Any]) -> str:
    """Return the terminal AIMessage text (verifier's precondition)."""
    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    assert isinstance(last, AIMessage), (
        f"terminal message must be AIMessage; got {type(last).__name__}"
    )
    content = last.content if isinstance(last.content, str) else str(last.content)
    return content


# ---------------------------------------------------------------------------
# 1. ADA guideline question - pure W-EVD path, no upload
# ---------------------------------------------------------------------------


async def test_e2e_evidence_path_against_real_corpus() -> None:
    """Real classifier + supervisor + Cohere retrieval + verifier.

    Confirms:
    * classifier picks W-EVD on a guideline-shaped question
    * supervisor dispatches ``evidence_retriever`` exactly once and then
      synthesises (no re-dispatch loop)
    * Cohere returns chunks from the indexed corpus, refs make it into
      ``fetched_refs``
    * the AIMessage cites ``guideline:<chunk_id>`` and the verifier allows
    * decision == "allow"
    """
    settings = Settings()
    conv_id = f"e2e-evd-{uuid.uuid4().hex[:8]}"

    final = await _run_graph_turn(
        settings=settings,
        conversation_id=conv_id,
        user_message=(
            "What does ADA recommend as an A1c target for a 65-year-old with T2D?"
        ),
    )

    text = _last_ai_text(final)
    assert "guideline:" in text, f"expected guideline citation in answer; got {text[:300]!r}"

    fetched = set(final.get("fetched_refs") or [])
    guideline_refs = [r for r in fetched if r.startswith("guideline:")]
    assert guideline_refs, f"no guideline refs in fetched_refs; got {fetched!r}"

    assert final.get("decision") == "allow", (
        f"expected decision=allow; got {final.get('decision')!r}"
    )

    iters = int(final.get("supervisor_iterations") or 0)
    assert 0 < iters <= 2, f"supervisor iterated {iters}x (expected 1-2 round trips)"


# ---------------------------------------------------------------------------
# 2. Document upload + extraction - full W-DOC path
# ---------------------------------------------------------------------------


async def test_e2e_upload_then_extract_lab_pdf() -> None:
    """End-to-end of the upload flow that /upload + chat compose for the user.

    Mirrors ``server.py:upload`` then a follow-up chat turn:
    1. Hit the real OpenEMR Standard API to land the bytes and recover
       the real ``DocumentReference`` id even if the upstream image emits
       the bool-given serializer failure.
    2. Inject the ``[system] Document uploaded: ...`` sentinel.
    3. Fire the synthetic chat turn the UI auto-posts after upload.

    Confirms:
    * classifier picks W-DOC because the sentinel is in state
    * supervisor dispatches ``intake_extractor``
    * worker actually invokes ``extract_document`` (or ``attach_document``
      -> ``extract_document``) - i.e. the tool wiring fix from earlier
      today is durable
    * the terminal AIMessage references ``DocumentReference/`` and the
      verifier allows
    """
    if not CHEN_LIPID_PDF.exists():
        pytest.skip(f"fixture pdf missing: {CHEN_LIPID_PDF}")
    upload_missing = _missing_upload_env()
    if upload_missing:
        pytest.skip(
            "upload test additionally needs: " + ", ".join(upload_missing)
        )

    settings = Settings()
    conv_id = f"e2e-doc-{uuid.uuid4().hex[:8]}"
    patient_id = os.environ.get("E2E_PATIENT_UUID") or settings.admin_user_ids[0]

    # Step 1: real upload through DocumentClient (same path /upload uses).
    set_active_smart_token(settings.openemr_fhir_token.get_secret_value() or None)
    from copilot.extraction.document_client import DocumentClient

    document_client = DocumentClient(settings)
    file_bytes = CHEN_LIPID_PDF.read_bytes()
    ok, doc_id, err, _ms = await document_client.upload(
        patient_id=patient_id,
        file_data=file_bytes,
        filename=CHEN_LIPID_PDF.name,
        category="lab_pdf",
    )
    assert ok, f"openemr upload failed: {err}"
    assert doc_id, "upload returned no document_id"
    assert not doc_id.startswith("openemr-upload-"), (
        f"upload returned synthetic document id: {doc_id}"
    )

    # Step 2 + 3: sentinel + synthetic chat turn (mirrors what the UI
    # does in App.tsx ``handleUploaded`` after the upload returns).
    sentinel = SystemMessage(
        content=(
            f'[system] Document uploaded: lab_pdf "{CHEN_LIPID_PDF.name}" '
            f'(document_id: DocumentReference/{doc_id}) for Patient/{patient_id}'
        )
    )
    final = await _run_graph_turn(
        settings=settings,
        conversation_id=conv_id,
        user_message=(
            f"I just uploaded {CHEN_LIPID_PDF.name}. Walk me through what's notable."
        ),
        extra_messages=[sentinel],
    )

    text = _last_ai_text(final)
    assert (
        "DocumentReference/" in text
        or "document_ref" in (final.get("tool_results") or [{}])[0].get("name", "")
    ), f"expected DocumentReference citation; got {text[:300]!r}"

    handoffs = final.get("handoff_events") or []
    targets = [h.get("to_node") for h in handoffs]
    assert "intake_extractor" in targets, (
        f"intake_extractor was never dispatched; handoff targets: {targets}"
    )

    assert final.get("decision") == "allow", (
        f"expected decision=allow; got {final.get('decision')!r}"
    )

    count_after_first = await _document_extraction_count(
        dsn=settings.checkpointer_dsn,
        patient_id=patient_id,
        document_id=doc_id,
    )
    assert count_after_first >= 1, "first extraction should persist a cache row"

    second = await _run_graph_turn(
        settings=settings,
        conversation_id=conv_id,
        user_message=(
            f"For the same uploaded {CHEN_LIPID_PDF.name}, remind me of the LDL."
        ),
        extra_messages=[sentinel],
    )
    assert second.get("decision") == "allow", (
        f"expected second decision=allow; got {second.get('decision')!r}"
    )
    count_after_second = await _document_extraction_count(
        dsn=settings.checkpointer_dsn,
        patient_id=patient_id,
        document_id=doc_id,
    )
    assert count_after_second == count_after_first, (
        "second extraction should be cache-served, not persisted as a new VLM run"
    )


# ---------------------------------------------------------------------------
# 3. Mixed turn: chart fact + guideline ask - supervisor preference
# ---------------------------------------------------------------------------


async def test_e2e_mixed_chart_and_guideline_prefers_evidence() -> None:
    """Per the classifier prompt, mixed chart+guideline asks prefer W-EVD.

    Documents the routing rule from ``prompts.py:60-61`` (mixed ->
    W-EVD, supervisor will dispatch chart fetches as needed) against
    the real classifier model so a future model bump can't silently
    regress it.
    """
    settings = Settings()
    conv_id = f"e2e-mix-{uuid.uuid4().hex[:8]}"

    final = await _run_graph_turn(
        settings=settings,
        conversation_id=conv_id,
        user_message=(
            "For a patient on metformin with eGFR 45, does KDIGO recommend "
            "continuing or stopping the medication?"
        ),
    )

    workflow = final.get("workflow_id")
    assert workflow == "W-EVD", (
        f"mixed chart+guideline must route W-EVD per prompts.py:60-61; got {workflow!r}"
    )

    text = _last_ai_text(final)
    assert "guideline:" in text, f"expected guideline citation; got {text[:300]!r}"
    assert final.get("decision") == "allow"
