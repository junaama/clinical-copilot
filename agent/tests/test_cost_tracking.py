"""Per-encounter cost estimation (issue 012).

Pure-function module — every test asserts ``estimate_*`` output against
hand-computed numbers. No mocks, no I/O.
"""

from __future__ import annotations

from copilot.cost_tracking import (
    aggregate_turn_cost,
    estimate_call_cost,
    estimate_embed_cost,
    estimate_rerank_cost,
)


def test_estimate_call_cost_known_model_uses_table_rate() -> None:
    # gpt-4o-mini is $0.00015 / 1K input, $0.0006 / 1K output.
    cost = estimate_call_cost("gpt-4o-mini", input_tokens=1000, output_tokens=500)
    assert cost.cost_usd is not None
    assert abs(cost.cost_usd - (0.00015 + 0.0003)) < 1e-9
    assert cost.input_tokens == 1000
    assert cost.output_tokens == 500


def test_estimate_call_cost_unknown_model_returns_none() -> None:
    """Unknown models surface ``cost_usd=None`` so callers don't silently 0-out."""
    cost = estimate_call_cost("imaginary-model-9000", input_tokens=100, output_tokens=10)
    assert cost.cost_usd is None
    assert cost.input_tokens == 100


def test_estimate_call_cost_prefix_matches_family() -> None:
    """``claude-sonnet-4-6`` should match the ``claude-sonnet-4`` family rate."""
    cost = estimate_call_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=1000)
    assert cost.cost_usd is not None
    # Anthropic Sonnet 4: $0.003/1K in + $0.015/1K out = $0.018 for 1K each.
    assert abs(cost.cost_usd - 0.018) < 1e-9


def test_estimate_call_cost_longest_prefix_wins() -> None:
    """``gpt-4o-mini`` must NOT collapse onto the more expensive ``gpt-4o`` rate."""
    mini = estimate_call_cost("gpt-4o-mini", input_tokens=10_000, output_tokens=10_000)
    full = estimate_call_cost("gpt-4o", input_tokens=10_000, output_tokens=10_000)
    assert mini.cost_usd is not None and full.cost_usd is not None
    assert mini.cost_usd < full.cost_usd
    # gpt-4o-mini: 0.00015*10 + 0.0006*10 = 0.0075
    assert abs(mini.cost_usd - 0.0075) < 1e-9
    # gpt-4o: 0.0025*10 + 0.010*10 = 0.125
    assert abs(full.cost_usd - 0.125) < 1e-9


def test_estimate_embed_cost_per_1k() -> None:
    cost = estimate_embed_cost(total_tokens=10_000)
    assert cost.cost_usd is not None
    # cohere embed-v3: $0.0001 / 1K
    assert abs(cost.cost_usd - 0.001) < 1e-9


def test_estimate_rerank_cost_per_call() -> None:
    cost = estimate_rerank_cost(call_count=3)
    assert cost.cost_usd is not None
    # cohere rerank-v3: $0.002 / call
    assert abs(cost.cost_usd - 0.006) < 1e-9


def test_aggregate_turn_cost_sums_known_calls() -> None:
    calls = [
        estimate_call_cost("gpt-4o-mini", input_tokens=1000, output_tokens=500),
        estimate_embed_cost(total_tokens=5000),
        estimate_rerank_cost(call_count=1),
    ]
    summary = aggregate_turn_cost(calls)
    # 0.00045 + 0.0005 + 0.002 = 0.00295
    assert abs(summary.total_usd - 0.00295) < 1e-6
    assert "gpt-4o-mini" in summary.by_model
    assert summary.rate_unknown_models == []
    assert len(summary.calls) == 3


def test_aggregate_turn_cost_flags_unknown_models() -> None:
    """Unknown models contribute to ``rate_unknown_models`` but not to total."""
    calls = [
        estimate_call_cost("gpt-4o-mini", input_tokens=1000, output_tokens=0),
        estimate_call_cost("brand-new-model", input_tokens=2000, output_tokens=500),
    ]
    summary = aggregate_turn_cost(calls)
    # Only the known model contributes: 0.00015.
    assert abs(summary.total_usd - 0.00015) < 1e-9
    assert summary.rate_unknown_models == ["brand-new-model"]
    assert "brand-new-model" not in summary.by_model


def test_aggregate_turn_cost_dedupes_unknown_models() -> None:
    calls = [
        estimate_call_cost("mystery", input_tokens=100, output_tokens=10),
        estimate_call_cost("mystery", input_tokens=100, output_tokens=10),
    ]
    summary = aggregate_turn_cost(calls)
    assert summary.rate_unknown_models == ["mystery"]
    assert summary.total_usd == 0.0


def test_aggregate_empty_input_yields_zero_total() -> None:
    summary = aggregate_turn_cost([])
    assert summary.total_usd == 0.0
    assert summary.by_model == {}
    assert summary.calls == []
    assert summary.rate_unknown_models == []


def test_estimate_call_cost_zero_tokens_is_zero_cost() -> None:
    cost = estimate_call_cost("gpt-4o-mini", input_tokens=0, output_tokens=0)
    assert cost.cost_usd == 0.0
