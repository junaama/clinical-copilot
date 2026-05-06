"""Embed guideline chunks via Cohere and persist them in pgvector.

Run via ``python -m copilot.retrieval.indexer --corpus-dir ./data/guidelines``.

Idempotent: chunk_ids are content-derived (see ``corpus._chunk_id``), so
re-running the indexer over an unchanged corpus is a no-op. Existing
chunk_ids are skipped *before* the Cohere call so we don't pay to embed
chunks we've already stored.

The ``EmbeddingBackend`` protocol lets tests inject a stub embedder
without an API key. Production uses ``CohereEmbeddingBackend``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .corpus import GuidelineChunk, chunk_guideline
from .migrate import EMBEDDING_DIM, ensure_schema

_log = logging.getLogger(__name__)


# Cohere caps batch size at 96 for embed-english-v3 — keep some slack
# so we don't blow up on edge-case responses.
DEFAULT_EMBED_BATCH = 64

# embed-english-v3.0 is the dimension our schema is pinned to.
DEFAULT_EMBED_MODEL = "embed-english-v3.0"


# ---------------------------------------------------------------------------
# Embedding backend (protocol so tests can inject a deterministic stub)
# ---------------------------------------------------------------------------


class EmbeddingBackend(Protocol):
    """Anything that can turn ``texts`` into 1024-dim vectors."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class CohereEmbeddingBackend:
    """Production embedder. Uses ``input_type='search_document'`` because
    we're indexing the corpus side; the retriever uses ``search_query``.
    """

    api_key: str
    model: str = DEFAULT_EMBED_MODEL

    def __post_init__(self) -> None:
        try:
            import cohere
        except ImportError as exc:
            raise RuntimeError(
                "CohereEmbeddingBackend requires the 'retrieval' extra. "
                "Install with: uv sync --extra retrieval"
            ) from exc
        self._client = cohere.ClientV2(api_key=self.api_key)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        # ClientV2.embed returns ``EmbedByTypeResponse`` with embeddings
        # nested under ``.embeddings.float_``. The shape is stable across
        # the v2 SDK; if Cohere flips it under us this raises a clean
        # AttributeError which the indexer surfaces as a per-batch
        # failure.
        result = self._client.embed(
            texts=list(texts),
            model=self.model,
            input_type="search_document",
            embedding_types=["float"],
        )
        embeddings = result.embeddings.float_  # type: ignore[attr-defined]
        return [list(vec) for vec in embeddings]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _open_pool(dsn: str) -> AsyncIterator[object]:
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "retrieval.indexer requires the 'postgres' extra. "
            "Install with: uv sync --extra postgres"
        ) from exc
    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _existing_chunk_ids(pool: object, chunk_ids: Sequence[str]) -> set[str]:
    """Return the subset of ``chunk_ids`` that are already in the table."""
    if not chunk_ids:
        return set()
    async with pool.connection() as conn:  # type: ignore[attr-defined]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT chunk_id FROM guideline_chunks WHERE chunk_id = ANY(%s)",
                (list(chunk_ids),),
            )
            rows = await cur.fetchall()
    return {row[0] for row in rows}


async def _insert_chunks(
    pool: object,
    chunks: Sequence[GuidelineChunk],
    embeddings: Sequence[Sequence[float]],
) -> int:
    """INSERT … ON CONFLICT DO NOTHING. Returns rows actually written."""
    if not chunks:
        return 0
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunk/embedding length mismatch: {len(chunks)} vs {len(embeddings)}"
        )
    inserted = 0
    async with pool.connection() as conn:  # type: ignore[attr-defined]
        async with conn.cursor() as cur:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                if len(embedding) != EMBEDDING_DIM:
                    raise ValueError(
                        f"embedding dim {len(embedding)} != expected {EMBEDDING_DIM}"
                    )
                # pgvector accepts the vector literal as a string of the
                # form '[0.1,0.2,...]'. Going through the string form
                # avoids a hard import of the optional ``pgvector``
                # adapter at module load.
                vec_literal = "[" + ",".join(f"{v:.7f}" for v in embedding) + "]"
                await cur.execute(
                    """
                    INSERT INTO guideline_chunks
                        (chunk_id, guideline, section, page, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (chunk_id) DO NOTHING
                    """,
                    (
                        chunk.chunk_id,
                        chunk.guideline,
                        chunk.section,
                        chunk.page,
                        chunk.content,
                        vec_literal,
                    ),
                )
                inserted += cur.rowcount or 0
    return inserted


# ---------------------------------------------------------------------------
# Top-level indexing entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexResult:
    """Per-corpus summary returned by :func:`index_corpus`. Useful for the
    CLI's final log line and for assertions in integration tests."""

    documents_seen: int
    chunks_total: int
    chunks_skipped: int  # already in DB
    chunks_inserted: int


def _batched(items: Sequence[GuidelineChunk], n: int) -> Iterable[Sequence[GuidelineChunk]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


async def index_corpus(
    *,
    dsn: str,
    corpus_dir: str | Path,
    embedder: EmbeddingBackend,
    batch_size: int = DEFAULT_EMBED_BATCH,
) -> IndexResult:
    """Walk ``corpus_dir`` for ``*.pdf``, chunk them, embed missing
    chunks, INSERT.

    Idempotent: chunks already present (by chunk_id) are filtered out
    before embedding so re-runs are cheap.
    """
    corpus = Path(corpus_dir)
    if not corpus.is_dir():
        raise FileNotFoundError(f"corpus dir does not exist: {corpus}")

    pdfs = sorted(corpus.glob("*.pdf"))
    docs_seen = 0
    chunks_total = 0
    chunks_skipped = 0
    chunks_inserted = 0

    async with _open_pool(dsn) as pool:
        # Make sure the schema exists before we try to query it. This
        # lets ``index_corpus`` be a one-shot deploy step without a
        # separate migrate invocation.
        await ensure_schema(dsn)

        for pdf_path in pdfs:
            docs_seen += 1
            guideline = pdf_path.stem
            chunks = chunk_guideline(pdf_path, guideline)
            chunks_total += len(chunks)
            if not chunks:
                _log.warning("no chunks produced for %s", pdf_path.name)
                continue

            existing = await _existing_chunk_ids(pool, [c.chunk_id for c in chunks])
            new_chunks = [c for c in chunks if c.chunk_id not in existing]
            chunks_skipped += len(chunks) - len(new_chunks)

            for batch in _batched(new_chunks, batch_size):
                embeddings = embedder.embed_documents([c.content for c in batch])
                inserted = await _insert_chunks(pool, batch, embeddings)
                chunks_inserted += inserted

            _log.info(
                "indexed %s: %d total / %d new / %d skipped",
                pdf_path.name,
                len(chunks),
                len(new_chunks),
                len(chunks) - len(new_chunks),
            )

    return IndexResult(
        documents_seen=docs_seen,
        chunks_total=chunks_total,
        chunks_skipped=chunks_skipped,
        chunks_inserted=chunks_inserted,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("CHECKPOINTER_DSN", ""),
        help="Postgres DSN (defaults to $CHECKPOINTER_DSN).",
    )
    parser.add_argument(
        "--corpus-dir",
        default="./data/guidelines",
        help="Directory containing *.pdf clinical guidelines.",
    )
    parser.add_argument(
        "--cohere-api-key",
        default=os.environ.get("COHERE_API_KEY", ""),
        help="Cohere API key (defaults to $COHERE_API_KEY).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_EMBED_MODEL,
        help=f"Cohere embed model (default: {DEFAULT_EMBED_MODEL}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_EMBED_BATCH,
        help=f"Cohere embed batch size (default: {DEFAULT_EMBED_BATCH}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    if not args.dsn:
        _log.error("no DSN supplied: pass --dsn or set CHECKPOINTER_DSN")
        return 2
    if not args.cohere_api_key:
        _log.error("no Cohere API key: pass --cohere-api-key or set COHERE_API_KEY")
        return 2
    embedder = CohereEmbeddingBackend(api_key=args.cohere_api_key, model=args.model)
    result = asyncio.run(
        index_corpus(
            dsn=args.dsn,
            corpus_dir=args.corpus_dir,
            embedder=embedder,
            batch_size=args.batch_size,
        )
    )
    _log.info(
        "indexing complete: %d docs, %d chunks total (%d inserted, %d skipped)",
        result.documents_seen,
        result.chunks_total,
        result.chunks_inserted,
        result.chunks_skipped,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
