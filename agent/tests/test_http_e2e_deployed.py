"""HTTP-level e2e against the deployed agent (issue 030).

This suite mirrors what a real browser session does: it talks to the
deployed agent at the public Railway URL using a session cookie captured
from a manual login. It verifies that the post-issue-022 (id recovery)
and post-issue-023 (cache-first extraction) fixes hold in production —
where the existing ``test_graph_e2e_live.py::test_e2e_upload_then_extract_lab_pdf``
case never reaches because it gates on a static ``OPENEMR_FHIR_TOKEN``
that the deployed agent doesn't use (it speaks dynamic SMART tokens).

Run::

    cd agent
    COPILOT_SESSION_COOKIE=... \
      uv run pytest -m live_http -v tests/test_http_e2e_deployed.py

Env::

    COPILOT_AGENT_BASE_URL    deployed agent base URL (defaults to the
                              Railway prod URL).
    COPILOT_SESSION_COOKIE    value of the ``copilot_session`` cookie
                              from a successful manual login.
    E2E_PATIENT_UUID          optional — patient uuid the session has
                              CareTeam access to. Falls back to
                              ``E2E_LIVE_HTTP_PATIENT_UUID`` and finally
                              to picking the first patient from /panel.

Cost: ~$0.10 per run — exactly one VLM call (the upload's first
extraction). The two chat turns sit on the cache after that. Wall
clock: 30-90 s, dominated by the VLM call and one cold-start LLM turn
on the synthesizer.

This test is intentionally narrow:
* upload responds with a real ``DocumentReference/<id>`` (no
  ``openemr-upload-`` synthetic prefix) — verifies issue 022.
* the post-upload chat turn cites the same id and returns
  ``decision == "allow"``.
* the second chat turn against the same document carries a non-empty
  ``state.cache_hits`` list — verifies issue 023.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DOCS = REPO_ROOT / "example-documents"
CHEN_LIPID_PDF = EXAMPLE_DOCS / "lab-results" / "p01-chen-lipid-panel.pdf"

DEFAULT_BASE_URL = "https://copilot-agent-production-3776.up.railway.app"

# Generous timeout — VLM extraction inside /upload can run 20+s on a
# cold Railway instance, and the synthesis chat turn can stack another
# 10-30s on top of that.
HTTP_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def _base_url() -> str:
    return os.environ.get("COPILOT_AGENT_BASE_URL") or DEFAULT_BASE_URL


def _session_cookie() -> str:
    return (
        os.environ.get("COPILOT_SESSION_COOKIE")
        or os.environ.get("COPILOT_TEST_SESSION_TOKEN")
        or ""
    )


def _missing_env() -> list[str]:
    """Return a list of missing env vars; empty when configured."""
    missing: list[str] = []
    if not _session_cookie():
        missing.append("COPILOT_SESSION_COOKIE")
    return missing


pytestmark = [
    pytest.mark.live_http,
    pytest.mark.skipif(
        bool(_missing_env()),
        reason=(
            "live_http e2e requires a session cookie; missing: "
            + ", ".join(_missing_env() or ["(none)"])
        ),
    ),
]


def _client() -> httpx.AsyncClient:
    """Return an httpx client preconfigured with the session cookie.

    Cookie name (``copilot_session``) matches ``server.SESSION_COOKIE_NAME``.
    httpx accepts the cookie via ``cookies={...}`` so the value is sent on
    every request without ever being persisted to disk.
    """
    return httpx.AsyncClient(
        base_url=_base_url(),
        cookies={"copilot_session": _session_cookie()},
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
    )


async def _resolve_patient_id(client: httpx.AsyncClient) -> str:
    """Return a patient uuid the session has access to.

    Order of preference:
    1. ``E2E_PATIENT_UUID`` — explicit override (matches the existing
       live-suite env name).
    2. ``E2E_LIVE_HTTP_PATIENT_UUID`` — live_http-specific override so a
       different cohort can be used without disturbing the in-process
       live suite's choice.
    3. The first patient on the session's ``/panel`` roster — falls
       back gracefully when neither override is set, but skips when the
       roster is empty (e.g., session belongs to a practitioner without
       an assigned panel).
    """
    explicit = os.environ.get("E2E_PATIENT_UUID") or os.environ.get(
        "E2E_LIVE_HTTP_PATIENT_UUID"
    )
    if explicit:
        return explicit

    resp = await client.get("/panel")
    if resp.status_code == 401:
        pytest.skip(
            "session cookie rejected by /panel — re-capture from a fresh login "
            "and re-export COPILOT_SESSION_COOKIE"
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
    """Mint a fresh conversation id via POST /conversations.

    Mirrors what the UI does on first /chat. Returns the registry-stored
    id so subsequent /chat calls land in the same thread and the
    checkpointer carries the upload sentinel forward.
    """
    resp = await client.post("/conversations")
    if resp.status_code == 401:
        pytest.skip("session cookie rejected by /conversations — re-capture")
    resp.raise_for_status()
    return str(resp.json()["id"])


async def _upload_lab_pdf(
    client: httpx.AsyncClient,
    *,
    patient_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    """POST the lab-pdf fixture to /upload and return the response body.

    Uses the synthetic Chen lipid panel checked into ``example-documents/``
    — no PHI risk. The endpoint runs the VLM pipeline inline so this
    request is the slow one (20+ s).
    """
    file_bytes = CHEN_LIPID_PDF.read_bytes()
    files = {"file": (CHEN_LIPID_PDF.name, file_bytes, "application/pdf")}
    data = {
        "patient_id": patient_id,
        "doc_type": "lab_pdf",
        "conversation_id": conversation_id,
    }
    resp = await client.post("/upload", files=files, data=data)
    if resp.status_code == 401:
        pytest.skip("session cookie rejected by /upload — re-capture")
    resp.raise_for_status()
    return resp.json()


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


async def test_deployed_upload_then_chat_then_cached_chat() -> None:
    """End-to-end smoke against the deployed agent.

    Verifies the three production-side claims:

    1. Issue 022 (id recovery): /upload returns a real
       ``DocumentReference/<id>`` — no ``openemr-upload-<hex>`` synthetic
       prefix. Reverting 022 makes this fail.
    2. The post-upload chat turn cites the same real id and the
       verifier emits ``decision == "allow"``.
    3. Issue 023 (cache-first extraction): the second chat turn about
       the same document surfaces a non-empty ``state.cache_hits`` —
       proving the extract was cache-served, not re-VLM'd. Reverting
       023's cache-first branch makes this fail (the second turn
       runs a fresh extraction, ``cache_hits`` stays empty).
    """
    if not CHEN_LIPID_PDF.exists():
        pytest.skip(f"fixture pdf missing: {CHEN_LIPID_PDF}")

    async with _client() as client:
        patient_id = await _resolve_patient_id(client)
        conversation_id = await _new_conversation_id(client)

        upload = await _upload_lab_pdf(
            client, patient_id=patient_id, conversation_id=conversation_id
        )

        assert upload.get("status") == "ok", (
            f"upload did not return ok status; got: {upload!r}"
        )
        document_reference = upload.get("document_reference") or ""
        assert "DocumentReference/" in document_reference, (
            f"upload payload missing canonical DocumentReference; got: {upload!r}"
        )
        assert "openemr-upload-" not in document_reference, (
            f"upload returned synthetic doc-ref (issue 022 regression?); "
            f"got: {document_reference!r}"
        )

        # First chat turn: notable-findings walk-through. The classifier
        # picks W-DOC because the upload sentinel is in checkpointer
        # state; the supervisor dispatches the intake_extractor worker;
        # the synthesizer cites the canonical DocumentReference.
        first_chat = await _chat_turn(
            client,
            conversation_id=conversation_id,
            patient_id=patient_id,
            message=(
                f"I just uploaded {CHEN_LIPID_PDF.name}. Walk me through what's "
                "notable."
            ),
        )
        first_reply = first_chat.get("reply") or ""
        assert document_reference in first_reply, (
            f"first chat reply did not cite {document_reference!r}; "
            f"reply preview: {first_reply[:300]!r}"
        )
        first_block = first_chat.get("block") or {}
        # ``decision == allow`` is the verifier's allow signal. The
        # ``/chat`` response shape doesn't surface ``decision`` directly
        # today, so we read it from the block kind: a refusal would land
        # as a plain block whose lead is the refusal phrase. The
        # presence of the canonical doc-ref in the reply already
        # establishes citation grounding; here we additionally guard
        # against the verifier's apology-fallback wording.
        assert "couldn't produce a verifiable response" not in first_reply, (
            "verifier produced the tool_failure refusal instead of allow"
        )
        assert "couldn't ground" not in first_reply, (
            "verifier refused with the unsourced-claim wording instead of allow"
        )
        assert first_block.get("kind"), (
            f"first chat returned no block kind; got: {first_block!r}"
        )

        # Second chat turn: same document, different question. Issue
        # 023's cache-first branch must serve the second extract from
        # the document_extractions table — the /chat response state
        # carries cache_hits accordingly.
        second_chat = await _chat_turn(
            client,
            conversation_id=conversation_id,
            patient_id=patient_id,
            message=(
                f"For the same uploaded {CHEN_LIPID_PDF.name}, remind me of "
                "the LDL value."
            ),
        )
        cache_hits = second_chat.get("state", {}).get("cache_hits") or []
        assert cache_hits, (
            "second chat turn carried no cache_hits — cache-first extraction "
            f"(issue 023) did not run; state: {second_chat.get('state')!r}"
        )
