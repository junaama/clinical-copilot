"""Per-encounter cost estimation (issue 012).

Each LLM/Cohere call has a per-1K-token rate; the agent's audit row records
prompt + completion tokens but not the dollar amount. ``estimate_call_cost``
turns ``(model, input_tokens, output_tokens)`` into a USD figure using a
small built-in rate table; ``estimate_turn_cost`` aggregates across the
calls a turn made (chat model, embedding, rerank, VLM page-by-page).

Rates are list-price 2026-Q2 — they will drift. Treat the absolute number as
indicative; treat the *trend* (per-turn, per-workflow, per-tier) as
actionable. When a model rate isn't in the table the estimator returns
``None`` rather than guessing — silent zeros would hide spend.

No I/O, no network. Pure functions over dataclasses, easy to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# USD per 1K tokens — list price as of 2026-Q2. Keys are normalized to
# lowercase prefix-match so ``"claude-sonnet-4-6"`` matches the
# ``"claude-sonnet-4"`` family entry.
#
# Anthropic rates: https://www.anthropic.com/pricing
# OpenAI rates: https://openai.com/api/pricing/
# Cohere rates: https://cohere.com/pricing
_TEXT_RATES_PER_1K: dict[str, tuple[float, float]] = {
    # (input_per_1k, output_per_1k)
    "claude-opus-4": (0.015, 0.075),
    "claude-sonnet-4": (0.003, 0.015),
    "claude-haiku-4": (0.001, 0.005),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-5-haiku": (0.0008, 0.004),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.010),
    "gpt-4-turbo": (0.010, 0.030),
}

# Cohere embed/rerank are flat per-call or per-1K-tokens. The unit here is
# USD per 1K tokens for embed (parity with chat) and USD per call for rerank.
_EMBED_RATE_PER_1K = 0.0001  # cohere embed-english-v3.0
_RERANK_RATE_PER_CALL = 0.002  # cohere rerank-english-v3.0, $2/1k searches


@dataclass(frozen=True)
class CallCost:
    """Cost line item for a single model invocation.

    ``model`` is the human-readable model name as reported by the provider
    (e.g. ``"gpt-4o-mini"``, ``"claude-sonnet-4-6"``, or ``"cohere-rerank"``).
    ``cost_usd`` is ``None`` when the model isn't in the rate table — caller
    should treat that as "rate unknown, do not aggregate" rather than zero.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 1
    cost_usd: float | None = None


@dataclass(frozen=True)
class TurnCost:
    """Aggregate cost for one agent turn across all model invocations."""

    total_usd: float
    by_model: dict[str, float] = field(default_factory=dict)
    calls: list[CallCost] = field(default_factory=list)
    rate_unknown_models: list[str] = field(default_factory=list)


def _lookup_text_rate(model: str) -> tuple[float, float] | None:
    """Find a rate entry by longest-prefix match on a normalized model name."""
    if not model:
        return None
    needle = model.lower().strip()
    # Longest-prefix match — ``"gpt-4o-mini"`` should NOT collapse onto
    # ``"gpt-4o"`` and pick the wrong (more expensive) rate.
    best_key: str | None = None
    for key in _TEXT_RATES_PER_1K:
        if needle.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        return None
    return _TEXT_RATES_PER_1K[best_key]


def estimate_call_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> CallCost:
    """Cost for one chat-model call. Returns ``cost_usd=None`` when unknown."""
    rate = _lookup_text_rate(model)
    if rate is None:
        return CallCost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=None,
        )
    in_rate, out_rate = rate
    cost = (input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate
    return CallCost(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost, 6),
    )


def estimate_embed_cost(*, total_tokens: int, model: str = "cohere-embed-v3") -> CallCost:
    """Cost for one Cohere embedding batch."""
    cost = (total_tokens / 1000.0) * _EMBED_RATE_PER_1K
    return CallCost(
        model=model,
        input_tokens=total_tokens,
        cost_usd=round(cost, 6),
    )


def estimate_rerank_cost(*, call_count: int = 1, model: str = "cohere-rerank-v3") -> CallCost:
    """Cost for ``call_count`` Cohere rerank invocations."""
    cost = call_count * _RERANK_RATE_PER_CALL
    return CallCost(
        model=model,
        call_count=call_count,
        cost_usd=round(cost, 6),
    )


def aggregate_turn_cost(calls: Iterable[CallCost]) -> TurnCost:
    """Sum cost across all calls in a turn, returning a structured breakdown.

    ``rate_unknown_models`` lists any models whose ``cost_usd`` was ``None``
    so callers can flag them rather than silently undercount.
    """
    by_model: dict[str, float] = {}
    rate_unknown: list[str] = []
    total = 0.0
    materialized: list[CallCost] = []
    for c in calls:
        materialized.append(c)
        if c.cost_usd is None:
            if c.model not in rate_unknown:
                rate_unknown.append(c.model)
            continue
        total += c.cost_usd
        by_model[c.model] = round(by_model.get(c.model, 0.0) + c.cost_usd, 6)
    return TurnCost(
        total_usd=round(total, 6),
        by_model=by_model,
        calls=materialized,
        rate_unknown_models=rate_unknown,
    )
