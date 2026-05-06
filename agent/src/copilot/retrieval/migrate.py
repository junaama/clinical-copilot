"""DB migration: pgvector extension + ``guideline_chunks`` table.

Run via ``python -m copilot.retrieval.migrate`` (env supplies
``CHECKPOINTER_DSN``) or ``python -m copilot.retrieval.migrate --dsn ...``.
The migration is fully idempotent — every statement is ``IF NOT EXISTS``
so it is safe to run on every deploy.

Schema is fixed by the W2 PRD; do not rename columns or change the
``vector`` dimension without coordinating with the Cohere embedding model
choice (``embed-english-v3.0`` → 1024 dims).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_log = logging.getLogger(__name__)


# Fixed by the embedding model (cohere/embed-english-v3.0 = 1024).
EMBEDDING_DIM = 1024

# IVFFLAT lists for ~thousands of chunks. The PRD targets ~150 pages of
# guideline text; even at one chunk per quarter-page that's <600 chunks,
# which fits comfortably under the rule-of-thumb (rows / 1000) for ivfflat
# list count. Bumping later is a one-line ALTER INDEX REBUILD.
_IVFFLAT_LISTS = 10

_DDL_STATEMENTS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""
    CREATE TABLE IF NOT EXISTS guideline_chunks (
        chunk_id    TEXT PRIMARY KEY,
        guideline   TEXT NOT NULL,
        section     TEXT,
        page        INT,
        content     TEXT NOT NULL,
        embedding   vector({EMBEDDING_DIM}),
        tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS guideline_chunks_embedding_idx
        ON guideline_chunks
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = {_IVFFLAT_LISTS})
    """,
    """
    CREATE INDEX IF NOT EXISTS guideline_chunks_tsv_idx
        ON guideline_chunks
        USING gin (tsv)
    """,
)


@asynccontextmanager
async def _open_pool(dsn: str) -> AsyncIterator[object]:
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "retrieval.migrate requires the 'postgres' extra. "
            "Install with: uv sync --extra postgres"
        ) from exc

    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def ensure_schema(dsn: str) -> None:
    """Create the pgvector extension, the ``guideline_chunks`` table, and the
    two indexes. Idempotent — every statement is ``IF NOT EXISTS``.

    Caller supplies the DSN explicitly so this is reusable from tests with
    a disposable Postgres without leaning on process env.
    """
    async with _open_pool(dsn) as pool:
        async with pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                for stmt in _DDL_STATEMENTS:
                    await cur.execute(stmt)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("CHECKPOINTER_DSN", ""),
        help="Postgres DSN (defaults to $CHECKPOINTER_DSN).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    if not args.dsn:
        _log.error("no DSN supplied: pass --dsn or set CHECKPOINTER_DSN")
        return 2
    asyncio.run(ensure_schema(args.dsn))
    _log.info("guideline_chunks schema ensured")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
