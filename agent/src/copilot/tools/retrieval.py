"""Retrieval tool: ``retrieve_evidence`` (issue 008).

Wraps ``copilot.retrieval.retriever.Retriever`` as a LangChain
``StructuredTool`` so the supervisor's evidence-retriever worker (issue
009) can call it. Returns a dict with the ``(ok, rows, sources_checked,
error, latency_ms)`` envelope used by every other tool, plus a flat
``chunks`` list of ``EvidenceChunk`` payloads.

Authorization: guideline retrieval is not patient-scoped, so the
CareTeam gate's per-patient check doesn't apply. The tool still requires
an authenticated session — an empty ``user_id`` contextvar (which
indicates we're outside a real session) returns ``no_active_user`` so
that ad-hoc anonymous calls cannot exfiltrate the corpus.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.tools import StructuredTool

from ..config import Settings
from ..retrieval.retriever import Retriever
from .helpers import get_active_user_id


def make_retrieval_tools(
    settings: Settings,
    *,
    retriever: Retriever | None = None,
) -> list[StructuredTool]:
    """Build the retrieval tool list. ``retriever`` is injectable for tests."""
    r = retriever or Retriever(settings)

    async def retrieve_evidence(
        query: str,
        top_k: int = 5,
        domain_filter: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve cited evidence from the clinical guideline corpus."""
        started = time.monotonic()

        user_id = get_active_user_id() or ""
        if not user_id:
            return {
                "ok": False,
                "rows": [],
                "chunks": [],
                "sources_checked": [],
                "error": "no_active_user",
                "latency_ms": int((time.monotonic() - started) * 1000),
            }

        if not (query or "").strip():
            return {
                "ok": False,
                "rows": [],
                "chunks": [],
                "sources_checked": [],
                "error": "empty_query",
                "latency_ms": int((time.monotonic() - started) * 1000),
            }

        try:
            chunks = await r.retrieve(
                query, top_k=top_k, domain_filter=domain_filter
            )
        except Exception as exc:
            return {
                "ok": False,
                "rows": [],
                "chunks": [],
                "sources_checked": ["guideline_corpus"],
                "error": f"retrieval_failed: {exc.__class__.__name__}",
                "latency_ms": int((time.monotonic() - started) * 1000),
            }

        latency_ms = int((time.monotonic() - started) * 1000)
        # Stamp each chunk with the canonical ``guideline_ref`` so the
        # supervisor's evidence-retriever worker (issue 009) can scrape
        # it into ``fetched_refs`` for the verifier to validate citations
        # against.
        chunk_payloads: list[dict[str, Any]] = []
        for c in chunks:
            payload = c.model_dump()
            payload["guideline_ref"] = f"guideline:{c.chunk_id}"
            chunk_payloads.append(payload)
        return {
            "ok": True,
            "rows": chunk_payloads,
            "chunks": chunk_payloads,
            "sources_checked": ["guideline_corpus"],
            "error": None,
            "latency_ms": latency_ms,
        }

    return [
        StructuredTool.from_function(
            coroutine=retrieve_evidence,
            name="retrieve_evidence",
            description=(
                "Search the clinical guideline corpus (JNC 8, ADA, KDIGO, "
                "IDSA, AHA/ACC) for evidence relevant to ``query``. Returns "
                "ranked chunks with full source citations "
                "(guideline name, section, page, chunk_id). Optional "
                "``domain_filter`` restricts results to a single guideline "
                "by name. Use this for any question that asks 'what do the "
                "guidelines say about X' or 'is Y supported by evidence'. "
                "Cite returned chunks as <cite ref=\"guideline:{chunk_id}\" "
                "source=\"{guideline_name}\" section=\"{section}\"/>."
            ),
        ),
    ]
