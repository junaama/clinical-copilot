"""Per-turn audit log (ARCHITECTURE.md §9 step 11, Appendix B).

Writes one JSON-Lines row per agent turn to ``settings.agent_audit_log_path``.
Schema matches the future Postgres ``agent_audit`` table so a swap is just
a write-target change. Free-text user prompts and assistant responses are
NOT written here — that goes in a separately-encrypted table per §9 step 11.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Settings

_log = logging.getLogger(__name__)
_write_lock = threading.Lock()


@dataclass(frozen=True)
class AuditEvent:
    """One row in the audit log. Field order matches future SQL columns."""

    ts: str
    conversation_id: str
    user_id: str
    patient_id: str
    turn_index: int
    workflow_id: str
    classifier_confidence: float
    decision: str
    regen_count: int
    tool_call_count: int
    fetched_ref_count: int
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    model_provider: str
    model_name: str
    error: str | None = None
    escalation_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def write_audit_event(event: AuditEvent, settings: Settings) -> None:
    """Append one event to the audit log.

    Errors are logged but never raised — the audit log going down must not
    break the agent loop. The log path's parent directory is created on
    first write.
    """
    path = settings.agent_audit_log_path
    if not path:
        return
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        line = json.dumps(asdict(event), sort_keys=True, default=str) + "\n"
        with _write_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as exc:
        _log.warning("audit write failed for conversation %s: %s", event.conversation_id, exc)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
