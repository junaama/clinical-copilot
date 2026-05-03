"""Thin Langfuse wrapper used by the eval runner.

Goals:
- Lazy import: importing this module must not require the langfuse package to
  be installed at runtime; the eval works without Langfuse, scores just don't
  get pushed.
- Graceful degradation: if Langfuse is configured but unreachable, log and
  continue. An eval run should never fail because of an observability outage.
- One trace per case, one score per metric. Trace metadata carries the case
  id, tier, workflow, and persona.

EVAL.md §5 covers the self-hosted Langfuse setup; §8 covers what gets pushed.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from ..config import Settings, get_settings

if TYPE_CHECKING:
    from .case import CaseResult

_log = logging.getLogger(__name__)


class LangfuseClient:
    """Wrapper around the Langfuse SDK with safe fallbacks.

    Use ``client.enabled`` to check whether pushes will actually happen — when
    Langfuse env vars are unset (the local-dev default) the client silently
    no-ops so tests still run.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._sdk: Any | None = None
        self.experiment_name: str = (
            self.settings.eval_experiment_name
            or os.environ.get("EVAL_EXPERIMENT_NAME")
            or f"local-{uuid.uuid4().hex[:8]}"
        )

        if self.settings.langfuse_enabled:
            try:
                from langfuse import Langfuse  # type: ignore[import-not-found]

                self._sdk = Langfuse(
                    host=self.settings.langfuse_host,
                    public_key=self.settings.langfuse_public_key.get_secret_value(),
                    secret_key=self.settings.langfuse_secret_key.get_secret_value(),
                )
                _log.info("langfuse client initialized; experiment=%s", self.experiment_name)
            except ImportError:
                _log.warning("langfuse package not installed; install via 'uv sync'")
            except Exception as exc:  # noqa: BLE001
                _log.warning("langfuse init failed; continuing without push: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._sdk is not None

    def record_case(self, result: "CaseResult") -> str | None:
        """Push a case's trace + scores. Returns the trace id, or ``None``.

        Uses the langfuse 4.x API surface: ``start_as_current_observation``
        for the span context, ``set_current_trace_io`` + ``update_current_span``
        for metadata, and ``score_current_trace`` for the per-metric scores.
        The legacy ``self._sdk.trace(...)`` API was removed when the SDK
        moved to OTLP transport against a v3 server.
        """
        if not self.enabled or self._sdk is None:
            return None
        try:
            with self._sdk.start_as_current_observation(
                name=result.case.id,
                as_type="span",
            ):
                # Trace-level input/output (visible at the top of the dashboard
                # row, not buried inside the span).
                self._sdk.set_current_trace_io(
                    input={"message": result.case.message},
                    output={
                        "response": result.response_text,
                        "citations": result.citations,
                    },
                )
                # Span-level metadata for filtering + drill-down.
                self._sdk.update_current_span(
                    metadata={
                        "tier": result.case.tier,
                        "workflow": result.case.workflow,
                        "user_role": result.case.user_role,
                        "patient_id": result.case.patient_id,
                        "experiment": self.experiment_name,
                        "passed": result.passed,
                        "decision": result.decision,
                        "failures": result.failures,
                    },
                )
                # Per-metric scores attached to the trace (so the dataset
                # view aggregates them).
                for metric, value, comment in _flatten_scores(result):
                    self._sdk.score_current_trace(
                        name=metric,
                        value=value,
                        comment=comment,
                    )
                trace_id = self._sdk.get_current_trace_id()

            self._sdk.flush()
            return trace_id
        except Exception as exc:  # noqa: BLE001
            _log.warning("langfuse push failed for case %s: %s", result.case.id, exc)
            return None

    def flush(self) -> None:
        if self._sdk is not None:
            try:
                self._sdk.flush()
            except Exception:  # noqa: BLE001
                _log.exception("langfuse flush failed")


def _flatten_scores(result: "CaseResult") -> list[tuple[str, float, str | None]]:
    """Convert the runner's nested scores dict into a flat list for Langfuse."""
    out: list[tuple[str, float, str | None]] = []

    out.append(("passed", 1.0 if result.passed else 0.0, None))
    out.append(("latency_ms", float(result.latency_ms), None))
    out.append(("cost_usd", float(result.cost_usd), None))
    out.append(("prompt_tokens", float(result.prompt_tokens), None))
    out.append(("completion_tokens", float(result.completion_tokens), None))

    cite_res = result.scores.get("citation_resolution", {}) or {}
    out.append(
        (
            "citation_resolution",
            float(cite_res.get("score", 0.0)),
            f"unresolved={cite_res.get('unresolved')}",
        )
    )

    cite_comp = result.scores.get("citation_completeness", {}) or {}
    out.append(
        (
            "citation_completeness",
            float(cite_comp.get("score", 0.0)),
            f"missing={cite_comp.get('missing')}",
        )
    )

    facts = result.scores.get("required_facts", {}) or {}
    out.append(
        (
            "required_facts_coverage",
            float(facts.get("score", 0.0)),
            f"missing={facts.get('missing')}",
        )
    )

    forbidden = result.scores.get("forbidden_claims", {}) or {}
    out.append(
        (
            "forbidden_claim_violations",
            float(forbidden.get("count", 0)),
            f"violations={forbidden.get('violations')}",
        )
    )

    leaks = result.scores.get("pid_leak", {}) or {}
    out.append(
        (
            "pid_leak_count",
            float(leaks.get("count", 0)),
            f"leaks={leaks.get('leaks')}",
        )
    )

    decision = result.scores.get("decision", {}) or {}
    out.append(
        (
            "decision_match",
            1.0 if decision.get("matched") else 0.0,
            f"got={decision.get('got')} expected={decision.get('expected')}",
        )
    )

    # Per-dimension binary verdicts under a stable namespace so dataset-level
    # rollups can read the same names regardless of which scoring dimensions
    # are wired in (substring, citation, faithfulness, trajectory, …).
    for name, dim in result.dimensions.items():
        out.append(
            (
                f"dimension.{name}",
                1.0 if dim.passed else 0.0,
                f"score={dim.score}",
            )
        )

    return out
