"""Unit tests for the ``retrieve_evidence`` LangChain tool (issue 008).

Coverage:
* Tool envelope shape: ``ok``, ``rows``, ``chunks``, ``sources_checked``,
  ``error``, ``latency_ms``.
* No-active-user short-circuit returns ``no_active_user`` without
  invoking the retriever.
* Empty query returns ``empty_query``.
* Retriever exception is caught and surfaced as ``retrieval_failed:
  <ExcClass>`` so the LLM never sees a raw stack trace.
* Successful retrieval renders chunks via ``model_dump`` so the payload
  is JSON-serializable.
"""

from __future__ import annotations

from collections.abc import Sequence

from copilot.config import Settings
from copilot.retrieval.retriever import Retriever, _Candidate
from copilot.tools.helpers import set_active_user_id
from copilot.tools.retrieval import make_retrieval_tools


def _settings() -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        COHERE_API_KEY="test-cohere",
        USE_FIXTURE_FHIR=True,
    )


def _stub_retriever(candidates: list[_Candidate]) -> Retriever:
    async def stub_embed(_: str) -> list[float]:
        return [0.0] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        return candidates

    async def stub_rerank(
        _q: str, docs: Sequence[str], top_k: int
    ) -> list[tuple[int, float]]:
        return [(i, 0.9 - i * 0.1) for i in range(min(top_k, len(docs)))]

    return Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank,
    )


def _make_tool(retriever: Retriever):
    tools = make_retrieval_tools(_settings(), retriever=retriever)
    assert len(tools) == 1
    assert tools[0].name == "retrieve_evidence"
    return tools[0]


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


async def test_no_active_user_returns_no_active_user_error() -> None:
    set_active_user_id(None)
    tool = _make_tool(_stub_retriever([]))
    result = await tool.coroutine(query="hypertension")

    assert result["ok"] is False
    assert result["error"] == "no_active_user"
    assert result["chunks"] == []
    assert result["rows"] == []
    assert result["sources_checked"] == []
    assert isinstance(result["latency_ms"], int)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


async def test_empty_query_returns_empty_query_error() -> None:
    set_active_user_id("practitioner-1")
    tool = _make_tool(_stub_retriever([]))
    result = await tool.coroutine(query="")

    assert result["ok"] is False
    assert result["error"] == "empty_query"


async def test_whitespace_query_returns_empty_query_error() -> None:
    set_active_user_id("practitioner-1")
    tool = _make_tool(_stub_retriever([]))
    result = await tool.coroutine(query="   ")

    assert result["ok"] is False
    assert result["error"] == "empty_query"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_successful_retrieval_returns_serializable_chunks() -> None:
    set_active_user_id("practitioner-1")
    candidates = [
        _Candidate(
            chunk_id=f"chunk-{i}",
            guideline=f"Guideline-{i}",
            section=f"Section {i}",
            page=i,
            content=f"content {i}",
            rrf_score=1.0 / (i + 1),
        )
        for i in range(3)
    ]
    tool = _make_tool(_stub_retriever(candidates))

    result = await tool.coroutine(query="A1C management", top_k=3)

    assert result["ok"] is True
    assert result["error"] is None
    assert result["sources_checked"] == ["guideline_corpus"]
    assert len(result["chunks"]) == 3
    assert result["chunks"] == result["rows"]

    first = result["chunks"][0]
    # model_dump shape: every EvidenceChunk field present
    assert first["chunk_id"] == "chunk-0"
    assert first["guideline_name"] == "Guideline-0"
    assert first["section"] == "Section 0"
    assert first["page"] == 0
    assert first["text"] == "content 0"
    assert first["relevance_score"] >= 0.0
    citation = first["source_citation"]
    assert citation["source_type"] == "guideline"
    assert citation["source_id"] == "chunk-0"
    assert citation["field_or_chunk_id"] == "chunk-0"


async def test_each_chunk_carries_guideline_ref_for_supervisor_worker() -> None:
    """Issue 009: the supervisor's evidence-retriever worker scrapes
    ``guideline_ref`` JSON keys out of tool messages and feeds them to
    ``fetched_refs``, which the verifier then validates citations
    against. The retrieval tool must emit ``guideline_ref`` on every
    chunk in the canonical ``guideline:{chunk_id}`` form so a citation
    like ``<cite ref="guideline:chunk-0"/>`` resolves cleanly.
    """
    set_active_user_id("practitioner-1")
    candidates = [
        _Candidate(
            chunk_id="chunk-0",
            guideline="JNC8",
            section="Step 2",
            page=4,
            content="thiazide first-line",
            rrf_score=0.9,
        ),
    ]
    tool = _make_tool(_stub_retriever(candidates))

    result = await tool.coroutine(query="hypertension", top_k=1)

    assert result["chunks"][0]["guideline_ref"] == "guideline:chunk-0"
    # ``rows`` mirrors ``chunks`` so legacy callers still see the ref.
    assert result["rows"][0]["guideline_ref"] == "guideline:chunk-0"


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


async def test_retriever_exception_returns_error_envelope() -> None:
    set_active_user_id("practitioner-1")

    class _ExplodingRetriever(Retriever):
        async def retrieve(  # type: ignore[override]
            self, _query: str, *, top_k: int = 5, domain_filter: str | None = None
        ):
            raise ConnectionError("postgres down")

    tool = _make_tool(_ExplodingRetriever(_settings()))
    result = await tool.coroutine(query="anything")

    assert result["ok"] is False
    assert result["error"] == "retrieval_failed: ConnectionError"
    assert result["chunks"] == []
    assert result["rows"] == []
    # sources_checked still reports we tried the corpus, for audit clarity
    assert result["sources_checked"] == ["guideline_corpus"]


async def test_domain_filter_is_threaded_through_to_retriever() -> None:
    set_active_user_id("practitioner-1")
    captured: dict[str, str | None] = {}

    class _CapturingRetriever(Retriever):
        async def retrieve(  # type: ignore[override]
            self, query: str, *, top_k: int = 5, domain_filter: str | None = None
        ):
            captured["query"] = query
            captured["domain_filter"] = domain_filter
            return []

    tool = _make_tool(_CapturingRetriever(_settings()))
    await tool.coroutine(query="metformin", domain_filter="ADA")

    assert captured["query"] == "metformin"
    assert captured["domain_filter"] == "ADA"


async def test_make_retrieval_tools_returns_one_tool() -> None:
    tools = make_retrieval_tools(_settings())
    assert len(tools) == 1
    assert tools[0].name == "retrieve_evidence"
    assert "guideline" in (tools[0].description or "").lower()
