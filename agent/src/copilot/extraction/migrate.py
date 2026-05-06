"""DB migration: ``document_extractions`` table for lab extraction storage.

Lab extractions don't have a FHIR write path on OpenEMR — the W2 PRD calls
this the document-annotation model: keep the structured extraction in the
agent's own Postgres and cite it via ``DocumentReference/{id}``. Intake
extractions go straight to OpenEMR via the Standard API (allergies,
medications, medical_problems) and FHIR (Patient demographics) and do not
need this table.

Run via ``python -m copilot.extraction.migrate`` (env supplies
``CHECKPOINTER_DSN``) or ``python -m copilot.extraction.migrate --dsn ...``.
Idempotent — every statement is ``IF NOT EXISTS``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_log = logging.getLogger(__name__)


_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS document_extractions (
        id              BIGSERIAL PRIMARY KEY,
        document_id     TEXT NOT NULL,
        patient_id      TEXT NOT NULL,
        doc_type        TEXT NOT NULL,
        extraction_json JSONB NOT NULL,
        bboxes_json     JSONB NOT NULL,
        filename        TEXT,
        content_sha256  TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    ALTER TABLE document_extractions
        ADD COLUMN IF NOT EXISTS filename TEXT
    """,
    """
    ALTER TABLE document_extractions
        ADD COLUMN IF NOT EXISTS content_sha256 TEXT
    """,
    """
    CREATE INDEX IF NOT EXISTS document_extractions_document_idx
        ON document_extractions (document_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS document_extractions_patient_idx
        ON document_extractions (patient_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS document_extractions_hash_idx
        ON document_extractions (patient_id, filename, content_sha256)
    """,
)


@asynccontextmanager
async def _open_pool(dsn: str) -> AsyncIterator[object]:
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "extraction.migrate requires the 'postgres' extra. "
            "Install with: uv sync --extra postgres"
        ) from exc

    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=2)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def ensure_schema(dsn: str) -> None:
    """Create the ``document_extractions`` table and its indexes.

    Caller supplies the DSN explicitly so the function is reusable from
    tests pointing at a disposable Postgres without leaning on process
    env.
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
    _log.info("document_extractions schema ensured")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
