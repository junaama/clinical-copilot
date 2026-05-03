"""Per-tier per-dimension scoreboard renderer.

Aggregates a list of ``CaseResult`` into a tier table that shows the pass
rate per dimension plus the overall (AND-gated) pass rate per tier. The
output is the table the v2 PRD's README scoreboard reads from.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .case import CaseResult
from .gates import evaluate_tier_gates


def tier_dimension_table(results: list[CaseResult]) -> dict[str, dict[str, Any]]:
    """Aggregate per-tier per-dimension pass rates.

    Returns a mapping ``tier -> { count, overall, dimensions: {name -> rate} }``.
    A dimension that no case in the tier scored does not appear in the dict;
    a dimension scored by some cases reports the rate over only those cases.
    """
    by_tier: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_tier[r.case.tier].append(r)

    table: dict[str, dict[str, Any]] = {}
    for tier, tier_results in by_tier.items():
        dim_pass: dict[str, int] = defaultdict(int)
        dim_total: dict[str, int] = defaultdict(int)
        for r in tier_results:
            for name, dim in r.dimensions.items():
                dim_total[name] += 1
                if dim.passed:
                    dim_pass[name] += 1

        dimensions = {
            name: dim_pass[name] / dim_total[name]
            for name in dim_total
        }
        passed_count = sum(1 for r in tier_results if r.passed)
        table[tier] = {
            "count": len(tier_results),
            "overall": passed_count / len(tier_results) if tier_results else 0.0,
            "dimensions": dimensions,
        }
    return table


def render_scoreboard(results: list[CaseResult]) -> str:
    """Render a tier-by-dimension-by-overall pass-rate table as plain text.

    Always returns at least a header line so callers can print unconditionally.
    A trailing ``gates`` block (issue 017) shows each tier's gate verdict so
    the scoreboard reads as ``merge OK`` / ``release blocked (<80%)`` / etc.
    """
    table = tier_dimension_table(results)
    if not table:
        return "scoreboard: (no results)"

    dimension_names = sorted({
        name
        for tier_data in table.values()
        for name in tier_data["dimensions"]
    })

    headers = ["tier", "n", *dimension_names, "overall"]
    rows: list[list[str]] = [headers]
    for tier in sorted(table):
        tier_data = table[tier]
        row = [tier, str(tier_data["count"])]
        for name in dimension_names:
            rate = tier_data["dimensions"].get(name)
            row.append(f"{rate * 100:5.1f}%" if rate is not None else "    -")
        row.append(f"{tier_data['overall'] * 100:5.1f}%")
        rows.append(row)

    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]
    lines: list[str] = []
    for ridx, row in enumerate(rows):
        padded = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(padded)
        if ridx == 0:
            lines.append("  ".join("-" * w for w in widths))

    verdicts = evaluate_tier_gates(results)
    if verdicts:
        lines.append("")
        lines.append("gates:")
        for tier in sorted(verdicts):
            lines.append(f"  {verdicts[tier].summary}")
    return "\n".join(lines)
