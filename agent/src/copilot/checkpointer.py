"""Checkpointer factory.

In-memory by default for dev/tests. If ``CHECKPOINTER_DSN`` is set, callers
can opt into the Postgres saver — kept behind the optional ``postgres`` extra
so the base install stays light.
"""

from __future__ import annotations

from typing import Protocol

from langgraph.checkpoint.memory import MemorySaver

from .config import Settings


class _CheckpointerLike(Protocol):
    pass


def build_checkpointer(settings: Settings) -> _CheckpointerLike:
    if not settings.checkpointer_dsn:
        return MemorySaver()

    try:
        from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "CHECKPOINTER_DSN is set but the 'postgres' extra is not installed. "
            "Install with: uv sync --extra postgres"
        ) from exc

    saver = PostgresSaver.from_conn_string(settings.checkpointer_dsn)
    saver.setup()
    return saver
