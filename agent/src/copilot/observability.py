"""Runtime observability — Langfuse callback handler factory.

Per EVAL.md §5 + §8: Langfuse self-hosted from day one. Eval runs already
push case-level traces (``copilot.eval.langfuse_client``); this module is the
*runtime* counterpart — every production /chat turn produces a trace tree
covering classifier → agent → verifier with token usage, model latencies,
and tool calls visible in the dashboard.

No-ops when Langfuse env vars are unset, so dev runs work without setup.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .config import Settings, get_settings

_log = logging.getLogger(__name__)

# Single env-derived handler shared across calls. Initialized lazily.
_handler: Any | None = None
_handler_settings_fingerprint: str = ""


def _fingerprint(settings: Settings) -> str:
    return (
        f"{settings.langfuse_host}|"
        f"{settings.langfuse_public_key.get_secret_value()}|"
        f"{settings.langfuse_project}"
    )


def get_callback_handler(settings: Settings | None = None) -> Any | None:
    """Return a ``langfuse.langchain.CallbackHandler`` or ``None`` if disabled.

    The handler is safe to attach to ``config={"callbacks": [...]}`` on
    every ``graph.ainvoke``. When Langfuse isn't configured, callers should
    omit it from the callbacks list rather than pass ``None`` (LangChain's
    callback registry rejects ``None`` entries).
    """
    global _handler, _handler_settings_fingerprint

    settings = settings or get_settings()
    if not settings.langfuse_enabled:
        return None

    fp = _fingerprint(settings)
    if _handler is not None and fp == _handler_settings_fingerprint:
        return _handler

    # Apply env vars so the SDK's get_client() picks them up. Use plain
    # assignment (NOT setdefault) — Settings is the source of truth, and a
    # stale env var from a prior run would otherwise pin the wrong host.
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key.get_secret_value()
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key.get_secret_value()

    try:
        # langfuse SDK 4.x exposes ``langfuse.langchain.CallbackHandler`` and
        # speaks OTLP to a v3 server. The full v3 stack is deployed
        # (Postgres + ClickHouse + Redis + MinIO + langfuse-web +
        # langfuse-worker) so traces should ingest cleanly.
        from langfuse.langchain import CallbackHandler

        _handler = CallbackHandler()
        _handler_settings_fingerprint = fp
        _log.info(
            "langfuse runtime callback handler initialized (host=%s, project=%s)",
            settings.langfuse_host,
            settings.langfuse_project,
        )
        return _handler
    except ImportError:
        _log.warning("langfuse package not installed; runtime traces disabled")
        return None
    except Exception as exc:
        _log.warning("langfuse runtime handler init failed: %s", exc)
        return None


def callback_config(settings: Settings | None = None) -> dict[str, Any]:
    """Build a ``config`` dict for ``graph.ainvoke`` with the handler attached.

    Returns ``{}`` when Langfuse is disabled — merge into your existing
    config dict, e.g. ``{**callback_config(), "configurable": {...}}``.
    """
    handler = get_callback_handler(settings)
    if handler is None:
        return {}
    return {"callbacks": [handler]}
