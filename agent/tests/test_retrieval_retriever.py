"""Unit tests for the hybrid retriever (issue 008).

Coverage:
* ``Retriever.retrieve`` happy path: mocked embedder + sql + reranker
  produce ``EvidenceChunk`` objects in the order Cohere returned them.
* Rerank actually reorders: a stub reranker that flips the candidate
  order produces evidence chunks in the flipped order.
* Cohere rerank failure falls back to RRF order with the RRF score
  copied into ``relevance_score``.
* Empty / whitespace query short-circuits to ``[]`` without calling
  any backend.
* ``top_k=0`` short-circuits to ``[]``.
* SQL builder emits the expected param names; the embedding becomes a
  pgvector literal.
* Coercion helpers handle both v1 and v2 cohere response shapes.
* Integration test (gated on ``COPILOT_TEST_PGVECTOR_DSN``) seeds a
  guideline corpus row and verifies the hybrid SQL retrieves it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence

import pytest

from copilot.config import Settings
from copilot.retrieval.retriever import (
    Retriever,
    _Candidate,
    _coerce_embeddings,
    _coerce_rerank,
    _format_pgvector,
    _hybrid_sql,
)
from copilot.retrieval.schemas import EvidenceChunk


def _settings() -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        COHERE_API_KEY="test-cohere",
        USE_FIXTURE_FHIR=True,
    )


def _candidate(idx: int, score: float = 0.0) -> _Candidate:
    return _Candidate(
        chunk_id=f"chunk-{idx}",
        guideline=f"Guideline-{idx}",
        section=f"Section {idx}",
        page=idx,
        content=f"content {idx}: lorem ipsum dolor sit amet",
        rrf_score=score,
    )


# ---------------------------------------------------------------------------
# retrieve(): happy path with stubbed embedder/sql/reranker
# ---------------------------------------------------------------------------


async def test_retrieve_happy_path_returns_evidence_chunks() -> None:
    candidates = [_candidate(i, score=1.0 / (i + 1)) for i in range(5)]

    async def stub_embed(_: str) -> list[float]:
        return [0.1] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        return candidates

    async def stub_rerank(
        _q: str, docs: Sequence[str], top_k: int
    ) -> list[tuple[int, float]]:
        # Cohere always returns indices into the supplied docs list with a
        # relevance_score in 0..1. Keep the original order for this test.
        return [(i, 0.9 - i * 0.1) for i in range(min(top_k, len(docs)))]

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank,
    )
    chunks = await retriever.retrieve("hypertension management", top_k=3)

    assert len(chunks) == 3
    assert all(isinstance(c, EvidenceChunk) for c in chunks)
    assert [c.chunk_id for c in chunks] == ["chunk-0", "chunk-1", "chunk-2"]
    # rerank scores are returned, not RRF scores
    assert chunks[0].relevance_score == pytest.approx(0.9)
    assert chunks[1].relevance_score == pytest.approx(0.8)
    # source citation correctly populated
    assert chunks[0].source_citation.source_type == "guideline"
    assert chunks[0].source_citation.source_id == "chunk-0"
    assert chunks[0].source_citation.page_or_section == "Section 0"


async def test_rerank_reorders_candidates() -> None:
    """Rerank that flips the order produces flipped output."""
    candidates = [_candidate(i, score=1.0) for i in range(3)]

    async def stub_embed(_: str) -> list[float]:
        return [0.1] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        return candidates

    async def stub_rerank_reverse(
        _q: str, docs: Sequence[str], top_k: int
    ) -> list[tuple[int, float]]:
        # Reverse the order — original index 2 first, then 1, then 0
        n = min(top_k, len(docs))
        return [(n - 1 - i, 0.9 - i * 0.05) for i in range(n)]

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank_reverse,
    )
    chunks = await retriever.retrieve("foo", top_k=3)

    assert [c.chunk_id for c in chunks] == ["chunk-2", "chunk-1", "chunk-0"]


# ---------------------------------------------------------------------------
# Fallback path: Cohere rerank fails → RRF order
# ---------------------------------------------------------------------------


async def test_rerank_failure_falls_back_to_rrf() -> None:
    candidates = [
        _candidate(0, score=0.9),
        _candidate(1, score=0.5),
        _candidate(2, score=0.3),
    ]

    async def stub_embed(_: str) -> list[float]:
        return [0.1] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        return candidates

    async def stub_rerank_explodes(
        _q: str, _docs: Sequence[str], _top_k: int
    ) -> list[tuple[int, float]]:
        raise RuntimeError("cohere unreachable")

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank_explodes,
    )
    chunks = await retriever.retrieve("foo", top_k=3)

    # Order preserved from RRF
    assert [c.chunk_id for c in chunks] == ["chunk-0", "chunk-1", "chunk-2"]
    # relevance_score copied from rrf_score
    assert chunks[0].relevance_score == pytest.approx(0.9)
    assert chunks[1].relevance_score == pytest.approx(0.5)


async def test_fallback_truncates_to_top_k() -> None:
    candidates = [_candidate(i, score=1.0 - i * 0.1) for i in range(8)]

    async def stub_embed(_: str) -> list[float]:
        return [0.1] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        return candidates

    async def stub_rerank_fail(
        _q: str, _docs: Sequence[str], _top_k: int
    ) -> list[tuple[int, float]]:
        raise OSError("broken")

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank_fail,
    )
    chunks = await retriever.retrieve("foo", top_k=5)

    assert len(chunks) == 5


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_empty_query_returns_empty_without_calling_backend() -> None:
    called = {"embed": 0, "sql": 0, "rerank": 0}

    async def stub_embed(_: str) -> list[float]:
        called["embed"] += 1
        return [0.0] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        called["sql"] += 1
        return []

    async def stub_rerank(
        _q: str, _docs: Sequence[str], _top_k: int
    ) -> list[tuple[int, float]]:
        called["rerank"] += 1
        return []

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank,
    )

    assert await retriever.retrieve("") == []
    assert await retriever.retrieve("   ") == []
    assert called == {"embed": 0, "sql": 0, "rerank": 0}


async def test_zero_top_k_short_circuits() -> None:
    async def stub_embed(_: str) -> list[float]:
        raise AssertionError("should not be called")

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        raise AssertionError("should not be called")

    async def stub_rerank(
        _q: str, _docs: Sequence[str], _top_k: int
    ) -> list[tuple[int, float]]:
        raise AssertionError("should not be called")

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank,
    )
    assert await retriever.retrieve("foo", top_k=0) == []


async def test_no_sql_candidates_returns_empty() -> None:
    async def stub_embed(_: str) -> list[float]:
        return [0.0] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], _domain: str | None
    ) -> list[_Candidate]:
        return []

    async def stub_rerank(
        _q: str, _docs: Sequence[str], _top_k: int
    ) -> list[tuple[int, float]]:
        raise AssertionError("rerank not called when no candidates")

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank,
    )
    assert await retriever.retrieve("foo") == []


async def test_domain_filter_propagates_to_sql_runner() -> None:
    captured: dict[str, str | None] = {}

    async def stub_embed(_: str) -> list[float]:
        return [0.0] * 1024

    async def stub_sql(
        _q: str, _emb: list[float], domain: str | None
    ) -> list[_Candidate]:
        captured["domain"] = domain
        return []

    async def stub_rerank(
        _q: str, _docs: Sequence[str], _top_k: int
    ) -> list[tuple[int, float]]:
        return []

    retriever = Retriever(
        _settings(),
        embedder=stub_embed,
        sql_runner=stub_sql,
        reranker=stub_rerank,
    )
    await retriever.retrieve("foo", domain_filter="JNC 8")

    assert captured["domain"] == "JNC 8"


# ---------------------------------------------------------------------------
# SQL builder
# ---------------------------------------------------------------------------


def test_hybrid_sql_includes_query_and_vector_params() -> None:
    sql, params = _hybrid_sql("hypertension", [0.1, 0.2, 0.3], None)

    assert params["q"] == "hypertension"
    assert params["qvec"] == "[0.1000000,0.2000000,0.3000000]"
    assert "domain" not in params
    # No domain clause when no filter
    assert "guideline ILIKE %(domain)s" not in sql
    # RRF k=60 hard-coded
    assert "1.0 / (60 + rank)" in sql


def test_hybrid_sql_with_domain_filter_adds_domain_param_and_clause() -> None:
    sql, params = _hybrid_sql("foo", [0.0] * 4, "ADA")

    # Substring match so the LLM can pass brand names like "ADA" or
    # "KDIGO" and still hit rows stored under the PDF stem
    # ("ada-diabetes-glycemic-2024", "kdigo-ckd-2024", ...).
    assert params["domain"] == "%ADA%"
    assert sql.count("guideline ILIKE %(domain)s") == 2  # both sparse and dense


def test_format_pgvector_renders_bracketed_csv() -> None:
    assert _format_pgvector([0.0, 1.0, -0.5]) == "[0.0000000,1.0000000,-0.5000000]"


# ---------------------------------------------------------------------------
# Cohere response coercion
# ---------------------------------------------------------------------------


class _FakeFloatEmbeddings:
    def __init__(self, rows: list[list[float]]) -> None:
        self.float_ = rows


class _FakeEmbedV2:
    def __init__(self, rows: list[list[float]]) -> None:
        self.embeddings = _FakeFloatEmbeddings(rows)


class _FakeEmbedV1:
    def __init__(self, rows: list[list[float]]) -> None:
        self.embeddings = rows


def test_coerce_embeddings_handles_v2_shape() -> None:
    response = _FakeEmbedV2([[0.1, 0.2], [0.3, 0.4]])
    assert _coerce_embeddings(response) == [[0.1, 0.2], [0.3, 0.4]]


def test_coerce_embeddings_handles_v1_shape() -> None:
    response = _FakeEmbedV1([[0.5, 0.6]])
    assert _coerce_embeddings(response) == [[0.5, 0.6]]


def test_coerce_embeddings_returns_empty_when_missing_attr() -> None:
    class _NoEmbeddings:
        pass

    assert _coerce_embeddings(_NoEmbeddings()) == []


class _FakeRerankResult:
    def __init__(self, index: int, score: float) -> None:
        self.index = index
        self.relevance_score = score


class _FakeRerankResponse:
    def __init__(self, results: list[_FakeRerankResult]) -> None:
        self.results = results


def test_coerce_rerank_extracts_index_and_score() -> None:
    response = _FakeRerankResponse(
        [_FakeRerankResult(2, 0.95), _FakeRerankResult(0, 0.42)]
    )
    assert _coerce_rerank(response) == [(2, 0.95), (0, 0.42)]


def test_coerce_rerank_handles_no_results() -> None:
    response = _FakeRerankResponse([])
    assert _coerce_rerank(response) == []


# ---------------------------------------------------------------------------
# Integration: live pgvector + real SQL runner (gated on env var)
# ---------------------------------------------------------------------------

_DSN = os.environ.get("COPILOT_TEST_PGVECTOR_DSN", "")


def _postgres_extra_available() -> bool:
    try:
        import psycopg  # noqa: F401

        return True
    except ImportError:
        return False


pgintegration = pytest.mark.skipif(
    not (_DSN and _postgres_extra_available()),
    reason=(
        "needs COPILOT_TEST_PGVECTOR_DSN (pgvector-enabled DB) and the "
        "'postgres' extra installed (uv sync --extra postgres)"
    ),
)


@pgintegration
async def test_hybrid_query_returns_seeded_chunk_in_top_5() -> None:
    """Seed one guideline chunk, query for it, expect it back via the
    real ``_default_sql_runner`` path (no Cohere — stub embedder/reranker).
    """
    from copilot.retrieval.migrate import EMBEDDING_DIM, ensure_schema

    await ensure_schema(_DSN)

    suffix = uuid.uuid4().hex[:8]
    guideline = f"itest-hyperten-{suffix}"
    chunk_id = f"chunk-{suffix}"
    content = (
        "For adults with hypertension, initial therapy should target a "
        "blood pressure goal of 140/90 mmHg per JNC 8 recommendations."
    )
    # Deterministic vector keyed off content so the dense rank is stable.
    seed = (sum(ord(c) for c in chunk_id) % 997) or 1
    embedding = [(seed * (i + 1)) % 1000 / 1000.0 for i in range(EMBEDDING_DIM)]
    qvec_literal = _format_pgvector(embedding)

    import psycopg

    async with await psycopg.AsyncConnection.connect(_DSN) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO guideline_chunks
                    (chunk_id, guideline, section, page, content, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (chunk_id) DO NOTHING
                """,
                (chunk_id, guideline, "Treatment", 12, content, qvec_literal),
            )
            await conn.commit()

    try:
        async def stub_embed(_: str) -> list[float]:
            return embedding

        async def stub_rerank(
            _q: str, docs: Sequence[str], top_k: int
        ) -> list[tuple[int, float]]:
            return [(i, 1.0 - i * 0.01) for i in range(min(top_k, len(docs)))]

        retriever = Retriever(
            _settings(),
            embedder=stub_embed,
            reranker=stub_rerank,
            dsn=_DSN,
        )

        chunks = await retriever.retrieve(
            "hypertension blood pressure goal",
            top_k=5,
            domain_filter=guideline,
        )

        assert any(c.chunk_id == chunk_id for c in chunks), (
            f"expected {chunk_id!r} in top-5; got {[c.chunk_id for c in chunks]!r}"
        )
        seeded = next(c for c in chunks if c.chunk_id == chunk_id)
        assert seeded.guideline_name == guideline
        assert seeded.section == "Treatment"
        assert seeded.page == 12
        assert seeded.source_citation.source_type == "guideline"
        assert seeded.source_citation.source_id == chunk_id
    finally:
        # Clean up the seeded row so re-runs stay idempotent on a shared DB.
        async with await psycopg.AsyncConnection.connect(_DSN) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM guideline_chunks WHERE chunk_id = %s",
                    (chunk_id,),
                )
                await conn.commit()
