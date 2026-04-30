"""Checkpointer factory.

Two modes:

- **Memory** (default for tests / dev / fixture mode): zero-setup, in-process,
  state lives only as long as the process. Returned synchronously.
- **Postgres** (production): durable conversation state via
  ``AsyncPostgresSaver``. Requires ``CHECKPOINTER_DSN`` set and the
  ``postgres`` extra installed (``uv sync --extra postgres``). Must be
  used as an async context manager because the underlying psycopg pool
  needs to be opened/closed cleanly.

Use ``open_checkpointer(settings)`` as the canonical entry point — it returns
an async context manager that yields a checkpointer regardless of mode, so
callers don't branch on configuration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from .config import Settings


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[Any]:
    """Yield a checkpointer for the lifetime of the ``async with`` block.

    Always works — falls back to ``MemorySaver`` when no DSN is configured,
    so dev and test paths don't need to know about Postgres.
    """
    if not settings.checkpointer_dsn:
        yield MemorySaver()
        return

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "CHECKPOINTER_DSN is set but the 'postgres' extra is not installed. "
            "Install with: uv sync --extra postgres"
        ) from exc

    async with AsyncPostgresSaver.from_conn_string(settings.checkpointer_dsn) as saver:
        # Idempotent — creates the checkpoint tables if missing.
        await saver.setup()
        yield saver


def build_memory_checkpointer() -> Any:
    """Direct constructor for the memory backend, used by sync entry points
    (tests, ad-hoc scripts) that don't need durable persistence.
    """
    return MemorySaver()
