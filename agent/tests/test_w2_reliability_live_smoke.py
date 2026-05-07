"""Live smoke for the Week 2 reliability slice (issue 029).

This is a focused live tier — narrower than ``test_graph_e2e_live.py`` and
parallel to ``test_http_e2e_deployed.py``. It pins the exact failures that
were observed in the deployed browser flow and that issues 024-028 closed:

* silent wrong-type uploads (intake fixture attached while ``lab_pdf`` was
  selected) — should now be surfaced as a 409 ``doc_type_mismatch`` rather
  than processed as a lab extraction (issue 024).
* canonical intake uploads — the response carries an ``intake`` payload and
  ``status == ok`` so the panel renders intake-shaped data (issue 025).
* post-upload chat consistency — the immediate next chat turn cites the
  same ``DocumentReference/<id>`` the upload returned, with no panel/chat
  contradiction (issue 026).
* visible guideline citations on representative ADA and KDIGO prompts —
  the chat block carries a ``guideline``-card citation rather than dropping
  the ``<cite/>`` tag (issues 027 + 028 fail-closed).

The smoke is **opt-in**. Default discovery filters out ``-m live_http``;
the suite skips cleanly when ``COPILOT_SESSION_COOKIE`` is unset so a
fresh laptop never fails.

Run::

    cd agent
    COPILOT_SESSION_COOKIE=<value-from-browser> \
      uv run pytest -m live_http -v tests/test_w2_reliability_live_smoke.py

Cost: ~$0.25 per full run (one VLM upload + four chat turns). Wall-clock:
60-180 s, dominated by the VLM call and the cold-start synthesizer turns.
The cases are independent — running a subset (e.g. just the ADA/KDIGO
cases) costs only the cited turns.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DOCS = REPO_ROOT / "example-documents"
INTAKE_PDF = EXAMPLE_DOCS / "intake-forms" / "p01-chen-intake-typed.pdf"

DEFAULT_BASE_URL = "https://copilot-agent-production-3776.up.railway.app"

HTTP_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


def _base_url() -> str:
    return os.environ.get("COPILOT_AGENT_BASE_URL") or DEFAULT_BASE_URL


def _session_cookie() -> str:
    return (
        os.environ.get("COPILOT_SESSION_COOKIE")
        or os.environ.get("COPILOT_TEST_SESSION_TOKEN")
        or ""
    )


def _missing_env() -> list[str]:
    missing: list[str] = []
    if not _session_cookie():
        missing.append("COPILOT_SESSION_COOKIE")
    return missing


pytestmark = [
    pytest.mark.live_http,
    pytest.mark.skipif(
        bool(_missing_env()),
        reason=(
            "live_http smoke requires a session cookie; missing: "
            + ", ".join(_missing_env() or ["(none)"])
        ),
    ),
]


def _client() -> httpx.AsyncClient:
    """Cookie-authed httpx client. ``copilot_session`` matches
    ``server.SESSION_COOKIE_NAME``; the value is sent on every request and
    never written to disk by the test.
    """
    return httpx.AsyncClient(
        base_url=_base_url(),
        cookies={"copilot_session": _session_cookie()},
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
    )


async def _resolve_patient_id(client: httpx.AsyncClient) -> str:
    """Pick a patient uuid for the upload tests.

    Order of preference: ``E2E_PATIENT_UUID`` → ``E2E_LIVE_HTTP_PATIENT_UUID``
    → first patient on the session's ``/panel`` roster. Skips cleanly when
    the session has no panel patients (practitioner with no CareTeam
    assignment).
    """
    explicit = os.environ.get("E2E_PATIENT_UUID") or os.environ.get(
        "E2E_LIVE_HTTP_PATIENT_UUID"
    )
    if explicit:
        return explicit
    resp = await client.get("/panel")
    if resp.status_code == 401:
        pytest.skip(
            "session cookie rejected by /panel — re-capture from a fresh login"
        )
    resp.raise_for_status()
    body = resp.json()
    patients = body.get("patients") or []
    if not patients:
        pytest.skip(
            "session has no panel patients; set E2E_PATIENT_UUID to a "
            "patient the practitioner has CareTeam access to"
        )
    return str(patients[0].get("patient_id"))


async def _new_conversation_id(client: httpx.AsyncClient) -> str:
    resp = await client.post("/conversations")
    if resp.status_code == 401:
        pytest.skip("session cookie rejected by /conversations — re-capture")
    resp.raise_for_status()
    return str(resp.json()["id"])


async def _upload(
    client: httpx.AsyncClient,
    *,
    fixture: Path,
    requested_type: str,
    patient_id: str,
    conversation_id: str,
    confirm: bool = False,
) -> httpx.Response:
    """POST a fixture to /upload and return the raw response.

    Returns the raw ``Response`` (not the parsed JSON) so callers can
    distinguish 200 (canonical envelope) from 409 (doc_type_mismatch
    rejection — issue 024).
    """
    file_bytes = fixture.read_bytes()
    files = {"file": (fixture.name, file_bytes, "application/pdf")}
    data: dict[str, str] = {
        "patient_id": patient_id,
        "doc_type": requested_type,
        "conversation_id": conversation_id,
    }
    if confirm:
        data["confirm_doc_type"] = "true"
    resp = await client.post("/upload", files=files, data=data)
    if resp.status_code == 401:
        pytest.skip("session cookie rejected by /upload — re-capture")
    return resp


async def _chat_turn(
    client: httpx.AsyncClient,
    *,
    conversation_id: str,
    patient_id: str,
    message: str,
) -> dict[str, Any]:
    body = {
        "conversation_id": conversation_id,
        "patient_id": patient_id,
        "message": message,
    }
    resp = await client.post("/chat", json=body)
    if resp.status_code == 401:
        pytest.skip("session cookie rejected by /chat — re-capture")
    resp.raise_for_status()
    return resp.json()


def _block_citations(chat: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the citation tuple from the response block as a list."""
    block = chat.get("block") or {}
    return list(block.get("citations") or [])


def _has_guideline_citation(citations: list[dict[str, Any]]) -> bool:
    """True iff at least one citation is a guideline source.

    Either the ``card`` is ``"guideline"`` or the ``fhir_ref`` carries the
    ``guideline:`` prefix — both shapes are produced by the post-027
    contract; tolerant matching here avoids coupling the smoke to the
    chip-label format.
    """
    for cite in citations:
        if cite.get("card") == "guideline":
            return True
        ref = cite.get("fhir_ref") or ""
        if isinstance(ref, str) and ref.startswith("guideline:"):
            return True
    return False


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def test_smoke_lab_mode_rejects_intake_fixture() -> None:
    """Issue 024: attaching an intake form while ``lab_pdf`` is selected
    must be surfaced, not silently processed.

    The deployed agent's deterministic guard (``detect_doc_type``) flags
    the fixture as ``intake_form`` with ``high`` confidence; without
    ``confirm_doc_type=true`` the upload returns HTTP 409 with a
    structured ``doc_type_mismatch`` body. A regression here (the guard
    disabled, the confidence threshold loosened, or the response shape
    changed) is what reverting issue 024 looked like in production.
    """
    if not INTAKE_PDF.exists():
        pytest.skip(f"fixture pdf missing: {INTAKE_PDF}")

    async with _client() as client:
        patient_id = await _resolve_patient_id(client)
        conversation_id = await _new_conversation_id(client)
        resp = await _upload(
            client,
            fixture=INTAKE_PDF,
            requested_type="lab_pdf",
            patient_id=patient_id,
            conversation_id=conversation_id,
        )

    assert resp.status_code == 409, (
        f"intake-as-lab upload was not rejected; status={resp.status_code} "
        f"body={resp.text[:300]!r}"
    )
    body = resp.json()
    detail = body.get("detail") or {}
    assert detail.get("code") == "doc_type_mismatch", (
        f"rejection body missing canonical mismatch code; got: {body!r}"
    )
    assert detail.get("requested_type") == "lab_pdf", (
        f"rejection body did not echo requested_type; got: {detail!r}"
    )
    assert detail.get("detected_type") == "intake_form", (
        f"rejection body did not surface detected_type; got: {detail!r}"
    )


async def test_smoke_intake_upload_then_chat_cites_same_document() -> None:
    """Issues 025 + 026: a successful intake upload returns an intake
    payload, and the immediate post-upload chat turn cites the same
    ``DocumentReference/<id>``.

    Combines two acceptance criteria into one transcript so the cited
    document id chain is exercised end-to-end with a single VLM call. A
    panel/chat contradiction (the deployed regression we observed) would
    surface either as ``intake`` payload missing on a 200 response, or
    as the chat reply omitting the doc-ref the upload just returned.
    """
    if not INTAKE_PDF.exists():
        pytest.skip(f"fixture pdf missing: {INTAKE_PDF}")

    async with _client() as client:
        patient_id = await _resolve_patient_id(client)
        conversation_id = await _new_conversation_id(client)

        resp = await _upload(
            client,
            fixture=INTAKE_PDF,
            requested_type="intake_form",
            patient_id=patient_id,
            conversation_id=conversation_id,
        )
        resp.raise_for_status()
        upload = resp.json()

        assert upload.get("status") == "ok", (
            f"intake upload did not return ok status; got: {upload!r}"
        )
        assert upload.get("discussable") is True, (
            f"intake upload not flagged discussable; got: {upload!r}"
        )
        intake_payload = upload.get("intake")
        assert intake_payload, (
            f"intake upload returned no intake payload (panel/chat would "
            f"disagree); got: {upload!r}"
        )
        # ``chief_concern`` is required by IntakeExtraction — its presence
        # is the cheapest assertion that the panel will render an
        # intake-oriented result, not an empty success.
        assert isinstance(intake_payload.get("chief_concern"), str), (
            f"intake payload missing chief_concern; got: {intake_payload!r}"
        )
        document_reference = upload.get("document_reference") or ""
        assert "DocumentReference/" in document_reference, (
            f"intake upload missing canonical doc-ref; got: {upload!r}"
        )
        assert "openemr-upload-" not in document_reference, (
            f"intake upload returned synthetic doc-ref (issue 022 regression); "
            f"got: {document_reference!r}"
        )

        chat = await _chat_turn(
            client,
            conversation_id=conversation_id,
            patient_id=patient_id,
            message=(
                f"I just uploaded {INTAKE_PDF.name}. What's the chief concern?"
            ),
        )

    reply = chat.get("reply") or ""
    assert document_reference in reply, (
        f"post-upload chat reply did not cite {document_reference!r}; "
        f"reply preview: {reply[:300]!r}"
    )
    assert "couldn't produce a verifiable response" not in reply, (
        "verifier produced the tool_failure refusal instead of allow"
    )
    assert "couldn't ground" not in reply, (
        "verifier refused with the unsourced-claim wording instead of allow"
    )


async def test_smoke_ada_a1c_question_returns_guideline_citation() -> None:
    """Issues 027 + 028: an ADA A1c-target question must produce a chat
    block whose citations carry a guideline source.

    The wire contract (issue 027) routes ``guideline:`` refs to a
    dedicated ``card == "guideline"`` so the frontend renders source
    chips without forcing the chart-card postMessage path. Issue 028
    closes the fail-closed gate so an uncited RAG answer never reaches
    the user. A regression on either path would either drop the
    citation entirely (027) or refuse the turn (028) — both fail this
    case.
    """
    async with _client() as client:
        patient_id = await _resolve_patient_id(client)
        conversation_id = await _new_conversation_id(client)
        chat = await _chat_turn(
            client,
            conversation_id=conversation_id,
            patient_id=patient_id,
            message=(
                "What does ADA recommend for A1c targets in adults with "
                "type 2 diabetes?"
            ),
        )

    citations = _block_citations(chat)
    assert citations, (
        f"ADA A1c chat block carried no citations; reply preview: "
        f"{(chat.get('reply') or '')[:300]!r}"
    )
    assert _has_guideline_citation(citations), (
        f"ADA A1c chat block carried citations but none were guideline-card; "
        f"got: {citations!r}"
    )


async def test_smoke_kdigo_ace_arb_question_returns_guideline_citation() -> None:
    """Issues 027 + 028: a KDIGO ACE/ARB question must produce a chat
    block whose citations carry a guideline source.

    Sister case to the ADA A1c test, exercising the second guideline
    corpus the demo relies on. Both prompts together are the W2 demo's
    representative RAG queries; either failing means the live demo
    regresses to the uncited-answer behaviour observed before issue 028.
    """
    async with _client() as client:
        patient_id = await _resolve_patient_id(client)
        conversation_id = await _new_conversation_id(client)
        chat = await _chat_turn(
            client,
            conversation_id=conversation_id,
            patient_id=patient_id,
            message=(
                "When does KDIGO recommend an ACE inhibitor or ARB for "
                "patients with chronic kidney disease?"
            ),
        )

    citations = _block_citations(chat)
    assert citations, (
        f"KDIGO ACE/ARB chat block carried no citations; reply preview: "
        f"{(chat.get('reply') or '')[:300]!r}"
    )
    assert _has_guideline_citation(citations), (
        f"KDIGO ACE/ARB chat block carried citations but none were "
        f"guideline-card; got: {citations!r}"
    )
