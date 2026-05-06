"""Hybrid retriever over the guideline corpus (issue 008).

Pipeline:

1. Embed the user query with Cohere ``embed-english-v3.0``
   (``input_type="search_query"``, 1024-dim).
2. Single Postgres query that combines sparse (``ts_rank`` over
   ``tsvector``) and dense (cosine distance over ``pgvector``) candidates
   via Reciprocal Rank Fusion (RRF, k=60), returning the top 20 chunks.
3. Pass those candidates through Cohere ``rerank-english-v3.0`` and keep
   the top ``top_k`` (default 5).
4. Wrap each surviving chunk in an ``EvidenceChunk`` with a
   ``SourceCitation`` pointing back at ``guideline:{chunk_id}``.

If Cohere rerank fails (network error, missing API key, transient 5xx),
the retriever returns the top-``top_k`` candidates by RRF score with a
``relevance_score`` taken straight off the RRF rank; the caller cannot
tell the path failed open without inspecting logs. This is deliberate:
guideline questions are clinically time-sensitive and a degraded answer
beats a refused one.

Both the Cohere SDK and ``psycopg`` are imported lazily inside methods so
the module is importable in environments that have neither extra
installed (the agent's base image, the unit-test runner). The first call
that actually needs them surfaces a clear ``RuntimeError`` if the extra
isn't there.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from .schemas import EvidenceChunk, SourceCitation

_log = logging.getLogger(__name__)

_RRF_K = 60
_CANDIDATE_LIMIT = 20
_PER_TIER_LIMIT = 50


@dataclass(frozen=True)
class _Candidate:
    """One row coming out of the hybrid SQL query, pre-rerank."""

    chunk_id: str
    guideline: str
    section: str | None
    page: int | None
    content: str
    rrf_score: float


# Public type aliases for testability — pass a stub for either to bypass
# the SDK / Postgres entirely in unit tests.
EmbeddingFn = Callable[[str], Awaitable[list[float]]]
RerankFn = Callable[[str, Sequence[str], int], Awaitable[list[tuple[int, float]]]]
SqlFn = Callable[[str, list[float], str | None], Awaitable[list[_Candidate]]]


class Retriever:
    """Hybrid (sparse + dense) retriever with Cohere rerank.

    Constructed with three callables — embedder, SQL runner, reranker —
    each defaulting to a real implementation but injectable for tests.
    The injection seam is the only public seam; ``retrieve`` is the only
    method users should call.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        embedder: EmbeddingFn | None = None,
        sql_runner: SqlFn | None = None,
        reranker: RerankFn | None = None,
        dsn: str | None = None,
    ) -> None:
        self._settings = settings
        self._dsn = dsn or settings.checkpointer_dsn
        self._embedder = embedder or self._default_embedder
        self._sql_runner = sql_runner or self._default_sql_runner
        self._reranker = reranker or self._default_reranker

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        domain_filter: str | None = None,
    ) -> list[EvidenceChunk]:
        """Retrieve the top ``top_k`` evidence chunks for ``query``.

        ``domain_filter`` constrains results to a single guideline by name
        (matches the ``guideline`` column exactly). Pass ``None`` to search
        the whole corpus.
        """
        normalized = (query or "").strip()
        if not normalized:
            return []
        if top_k <= 0:
            return []

        embedding = await self._embedder(normalized)
        candidates = await self._sql_runner(normalized, embedding, domain_filter)
        if not candidates:
            return []

        # Try Cohere rerank first; fall back to RRF order on any failure.
        reranked = await self._safe_rerank(normalized, candidates, top_k)
        if reranked is None:
            top = candidates[:top_k]
            return [self._to_evidence_chunk(c, c.rrf_score) for c in top]

        out: list[EvidenceChunk] = []
        for original_index, score in reranked:
            if 0 <= original_index < len(candidates):
                out.append(self._to_evidence_chunk(candidates[original_index], score))
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _safe_rerank(
        self, query: str, candidates: list[_Candidate], top_k: int
    ) -> list[tuple[int, float]] | None:
        try:
            return await self._reranker(
                query, [c.content for c in candidates], top_k
            )
        except Exception as exc:
            _log.warning(
                "cohere rerank failed; falling back to RRF order",
                extra={"exception_class": exc.__class__.__name__},
            )
            return None

    @staticmethod
    def _to_evidence_chunk(c: _Candidate, score: float) -> EvidenceChunk:
        citation = SourceCitation(
            source_type="guideline",
            source_id=c.chunk_id,
            page_or_section=c.section,
            field_or_chunk_id=c.chunk_id,
            quote_or_value=c.content[:240],
        )
        return EvidenceChunk(
            chunk_id=c.chunk_id,
            guideline_name=c.guideline,
            section=c.section,
            page=c.page,
            text=c.content,
            relevance_score=max(score, 0.0),
            source_citation=citation,
        )

    # ------------------------------------------------------------------
    # Default implementations (real Cohere / Postgres). Lazy imports so
    # the module stays importable without those extras.
    # ------------------------------------------------------------------

    async def _default_embedder(self, query: str) -> list[float]:
        try:
            import cohere  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "retrieve_evidence requires the 'retrieval' extra. "
                "Install with: uv sync --extra retrieval"
            ) from exc

        api_key = self._settings.cohere_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError(
                "COHERE_API_KEY is not set; cannot embed queries for retrieval"
            )
        client = cohere.AsyncClientV2(api_key=api_key)
        response = await client.embed(
            texts=[query],
            model="embed-english-v3.0",
            input_type="search_query",
            embedding_types=["float"],
        )
        embeddings = _coerce_embeddings(response)
        if not embeddings:
            raise RuntimeError("cohere embed returned no vectors")
        return embeddings[0]

    async def _default_reranker(
        self, query: str, documents: Sequence[str], top_k: int
    ) -> list[tuple[int, float]]:
        try:
            import cohere  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "retrieve_evidence requires the 'retrieval' extra"
            ) from exc

        api_key = self._settings.cohere_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("COHERE_API_KEY is not set")
        client = cohere.AsyncClientV2(api_key=api_key)
        response = await client.rerank(
            query=query,
            documents=list(documents),
            model="rerank-english-v3.0",
            top_n=top_k,
        )
        return _coerce_rerank(response)

    async def _default_sql_runner(
        self, query: str, embedding: list[float], domain_filter: str | None
    ) -> list[_Candidate]:
        if not self._dsn:
            raise RuntimeError(
                "no DSN configured; set CHECKPOINTER_DSN to enable retrieval"
            )
        try:
            import psycopg  # type: ignore[import-not-found]
            from psycopg.rows import dict_row  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "retrieve_evidence requires the 'postgres' extra"
            ) from exc

        sql, params = _hybrid_sql(query, embedding, domain_filter)
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return [_row_to_candidate(r) for r in rows]


# ---------------------------------------------------------------------------
# SQL builder + response coercion. Pulled out as module-level functions so
# they're independently testable.
# ---------------------------------------------------------------------------


def _hybrid_sql(
    query: str, embedding: list[float], domain_filter: str | None
) -> tuple[str, dict[str, Any]]:
    """Build the RRF-combined sparse+dense query.

    Both sub-queries cap at ``_PER_TIER_LIMIT`` rows; the union groups by
    ``chunk_id`` and sums ``1 / (k + rank)`` to produce the RRF score.
    The outer query keeps the top ``_CANDIDATE_LIMIT`` rows for rerank.
    """
    qvec_literal = _format_pgvector(embedding)
    domain_clause = "AND guideline ILIKE %(domain)s" if domain_filter else ""
    sql = f"""
        WITH sparse AS (
            SELECT chunk_id, guideline, section, page, content,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank(tsv, plainto_tsquery('english', %(q)s)) DESC
                   ) AS rank
            FROM guideline_chunks
            WHERE tsv @@ plainto_tsquery('english', %(q)s)
              {domain_clause}
            LIMIT {_PER_TIER_LIMIT}
        ),
        dense AS (
            SELECT chunk_id, guideline, section, page, content,
                   ROW_NUMBER() OVER (
                       ORDER BY embedding <=> %(qvec)s::vector
                   ) AS rank
            FROM guideline_chunks
            WHERE TRUE
              {domain_clause}
            ORDER BY embedding <=> %(qvec)s::vector
            LIMIT {_PER_TIER_LIMIT}
        ),
        combined AS (
            SELECT chunk_id, guideline, section, page, content, rank FROM sparse
            UNION ALL
            SELECT chunk_id, guideline, section, page, content, rank FROM dense
        )
        SELECT chunk_id,
               MAX(guideline)  AS guideline,
               MAX(section)    AS section,
               MAX(page)       AS page,
               MAX(content)    AS content,
               SUM(1.0 / ({_RRF_K} + rank)) AS rrf_score
        FROM combined
        GROUP BY chunk_id
        ORDER BY rrf_score DESC
        LIMIT {_CANDIDATE_LIMIT}
    """
    params: dict[str, Any] = {"q": query, "qvec": qvec_literal}
    if domain_filter:
        params["domain"] = f"%{domain_filter}%"
    return sql, params


def _format_pgvector(embedding: Sequence[float]) -> str:
    """Render an embedding as a pgvector literal: ``[0.1,0.2,…]``."""
    return "[" + ",".join(f"{float(v):.7f}" for v in embedding) + "]"


def _row_to_candidate(row: dict[str, Any]) -> _Candidate:
    return _Candidate(
        chunk_id=str(row["chunk_id"]),
        guideline=str(row.get("guideline") or ""),
        section=row.get("section"),
        page=row.get("page"),
        content=str(row.get("content") or ""),
        rrf_score=float(row.get("rrf_score") or 0.0),
    )


def _coerce_embeddings(response: object) -> list[list[float]]:
    """Pull ``[[float, ...], ...]`` out of either v1 or v2 cohere responses."""
    embeddings_obj = getattr(response, "embeddings", None)
    if embeddings_obj is None:
        return []
    # v2 returns ``EmbedByTypeResponseEmbeddings`` with ``.float_`` attr.
    float_attr = getattr(embeddings_obj, "float_", None) or getattr(
        embeddings_obj, "float", None
    )
    if float_attr is not None:
        return [list(row) for row in float_attr]
    # v1 returns a plain list[list[float]].
    if isinstance(embeddings_obj, list):
        return [list(row) for row in embeddings_obj]
    return []


def _coerce_rerank(response: object) -> list[tuple[int, float]]:
    """Pull ``[(index, relevance_score), ...]`` out of a cohere rerank response."""
    results = getattr(response, "results", None) or []
    out: list[tuple[int, float]] = []
    for r in results:
        idx = getattr(r, "index", None)
        score = getattr(r, "relevance_score", None)
        if idx is None or score is None:
            continue
        out.append((int(idx), float(score)))
    return out


# ---------------------------------------------------------------------------
# Module-level convenience: ``retrieve(...)`` for callers that don't want
# to manage a Retriever instance. Builds one with default settings.
# ---------------------------------------------------------------------------


async def retrieve(
    query: str,
    *,
    top_k: int = 5,
    domain_filter: str | None = None,
    settings: Settings | None = None,
) -> list[EvidenceChunk]:
    from ..config import get_settings

    s = settings or get_settings()
    retriever = Retriever(s)
    started = time.monotonic()
    try:
        return await retriever.retrieve(
            query, top_k=top_k, domain_filter=domain_filter
        )
    finally:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _log.debug(
            "retrieve completed",
            extra={"latency_ms": elapsed_ms, "top_k": top_k},
        )
