"""Tests for ``copilot.retrieval.indexer``.

Two layers:

- Unit tests with a stub ``EmbeddingBackend`` exercise the batching and
  CLI argument plumbing without needing an API key.
- Integration tests gated on ``COPILOT_TEST_PGVECTOR_DSN`` exercise the
  full pipeline end-to-end against a Postgres that has the ``vector``
  extension available. We deliberately use a *different* env var from
  the session-store integration tests (``COPILOT_TEST_PG_DSN``) because
  vanilla ``postgres:16-alpine`` doesn't have pgvector — this gate would
  silently fail on the stock dev DB.

Run the integration tests by spinning up pgvector locally:

    docker run --rm -d --name copilot-pgvector -p 5444:5432 \\
        -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres \\
        -e POSTGRES_DB=postgres pgvector/pgvector:pg16

    DSN='postgresql://postgres:postgres@localhost:5444/postgres?sslmode=disable'
    COPILOT_TEST_PGVECTOR_DSN="$DSN" uv run pytest tests/test_retrieval_indexer.py -v
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

from copilot.retrieval.indexer import (
    DEFAULT_EMBED_BATCH,
    EmbeddingBackend,
    IndexResult,
    _batched,
    index_corpus,
)
from copilot.retrieval.migrate import EMBEDDING_DIM, ensure_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDELINES_DIR = REPO_ROOT / "data" / "guidelines"

_DSN = os.environ.get("COPILOT_TEST_PGVECTOR_DSN", "")


def _postgres_extra_available() -> bool:
    """psycopg_pool ships in the optional 'postgres' extra. Tests that need
    a live DB also need the driver — skip cleanly when the extra is missing
    so a developer with no postgres extra installed sees a skip, not a
    crash."""
    try:
        import psycopg_pool  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Stub embedder
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Deterministic 1024-dim vector per text. Counts calls so tests can
    verify "skip already-indexed chunks before hitting the embedder"."""

    def __init__(self) -> None:
        self.call_count = 0
        self.texts_seen: list[str] = []

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.call_count += 1
        self.texts_seen.extend(texts)
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> list[float]:
        # Hash-derived deterministic vector so re-runs over the same text
        # produce the same value. Magnitude is irrelevant — the indexer
        # just needs EMBEDDING_DIM floats.
        seed = sum(ord(c) for c in text) or 1
        return [(seed * (i + 1)) % 1000 / 1000.0 for i in range(EMBEDDING_DIM)]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_batched_chunks_evenly() -> None:
    items = list(range(7))
    batches = list(_batched(items, 3))
    assert [list(b) for b in batches] == [[0, 1, 2], [3, 4, 5], [6]]


def test_batched_empty() -> None:
    assert list(_batched([], 5)) == []


def test_default_batch_under_cohere_cap() -> None:
    # Cohere embed-english-v3 caps at 96 — we should never default to
    # something that risks the cap.
    assert DEFAULT_EMBED_BATCH <= 96


def test_embedding_backend_is_protocol_compatible() -> None:
    # Static-typing parity: a stub satisfies the protocol.
    embedder: EmbeddingBackend = _StubEmbedder()
    out = embedder.embed_documents(["hello"])
    assert len(out) == 1
    assert len(out[0]) == EMBEDDING_DIM


# ---------------------------------------------------------------------------
# index_corpus error paths (no DB needed)
# ---------------------------------------------------------------------------


async def test_index_corpus_missing_dir_raises() -> None:
    with pytest.raises(FileNotFoundError):
        await index_corpus(
            dsn="postgresql://nowhere",
            corpus_dir="/tmp/does-not-exist-i-promise",
            embedder=_StubEmbedder(),
        )


# ---------------------------------------------------------------------------
# Full integration against pgvector (gated on COPILOT_TEST_PG_DSN)
# ---------------------------------------------------------------------------

pgintegration = pytest.mark.skipif(
    not (_DSN and _postgres_extra_available()),
    reason=(
        "needs COPILOT_TEST_PGVECTOR_DSN (pgvector-enabled DB) and the "
        "'postgres' extra installed (uv sync --extra postgres)"
    ),
)


@pgintegration
async def test_ensure_schema_idempotent() -> None:
    await ensure_schema(_DSN)
    await ensure_schema(_DSN)


@pgintegration
async def test_index_corpus_inserts_then_skips(tmp_path: Path) -> None:
    if not GUIDELINES_DIR.exists():
        pytest.skip("data/guidelines/ not in this checkout")

    # Copy fixtures under a unique prefix so re-runs across test sessions
    # don't collide on chunk_ids in a shared DB.
    suffix = uuid.uuid4().hex[:8]
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for src in GUIDELINES_DIR.glob("*.pdf"):
        shutil.copy(src, corpus / f"{src.stem}-{suffix}{src.suffix}")

    embedder = _StubEmbedder()
    first = await index_corpus(dsn=_DSN, corpus_dir=corpus, embedder=embedder)
    assert isinstance(first, IndexResult)
    assert first.documents_seen >= 3
    assert first.chunks_inserted > 0
    assert first.chunks_skipped == 0
    initial_call_count = embedder.call_count
    initial_texts_count = len(embedder.texts_seen)

    # Re-run: every chunk_id is already present, so nothing new is
    # inserted and the embedder is not called again on those chunks.
    second_embedder = _StubEmbedder()
    second = await index_corpus(dsn=_DSN, corpus_dir=corpus, embedder=second_embedder)
    assert second.chunks_total == first.chunks_total
    assert second.chunks_inserted == 0
    assert second.chunks_skipped == first.chunks_total
    assert second_embedder.call_count == 0  # nothing to embed
    assert second_embedder.texts_seen == []

    # Sanity: first run did something.
    assert initial_call_count > 0
    assert initial_texts_count > 0


@pgintegration
async def test_index_corpus_round_trip_query(tmp_path: Path) -> None:
    """After indexing, a chunk should be findable by exact content match
    (smoke check that rows landed). Vector-similarity retrieval is
    issue 008's job — this only verifies INSERT happened."""
    if not GUIDELINES_DIR.exists():
        pytest.skip("data/guidelines/ not in this checkout")

    suffix = uuid.uuid4().hex[:8]
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    src = next(iter(GUIDELINES_DIR.glob("jnc8-*.pdf")), None)
    if src is None:
        pytest.skip("JNC8 fixture missing")
    shutil.copy(src, corpus / f"{src.stem}-{suffix}{src.suffix}")

    embedder = _StubEmbedder()
    result = await index_corpus(dsn=_DSN, corpus_dir=corpus, embedder=embedder)
    assert result.chunks_inserted > 0

    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(_DSN, open=False, min_size=1, max_size=2)
    await pool.open()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM guideline_chunks WHERE guideline = %s",
                    (f"jnc8-hypertension-2014-{suffix}",),
                )
                row = await cur.fetchone()
                assert row is not None
                assert row[0] == result.chunks_inserted
    finally:
        await pool.close()
