"""Baseline file + regression detection for the Week 2 boolean rubric (issue 010).

The push gate compares the current run's per-rubric pass rates against
``.eval_baseline.json`` (committed at repo root). A rubric fails the gate if:

1. Its current rate is below the absolute floor in ``GATE_THRESHOLDS_W2``, OR
2. Its current rate dropped more than ``MAX_BASELINE_DROP`` from baseline.

The first check stops the gate from being gameable by lowering the baseline.
The second catches incremental degradation that's still above the floor.

The baseline file is JSON for human-diff readability. ``write_baseline`` is
the only sanctioned way to update it — runs intentionally raising the
baseline (e.g. after adding cases) call this with ``--write-baseline``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .w2_evaluators import GATE_THRESHOLDS_W2, MAX_BASELINE_DROP, RUBRIC_NAMES


@dataclass(frozen=True)
class CategoryVerdict:
    """Per-rubric gate outcome.

    ``passed`` is the binary verdict that contributes to the overall gate.
    ``reason`` is the one-line human-readable explanation the hook surfaces.
    """

    name: str
    current_rate: float
    baseline_rate: float
    threshold: float
    max_drop: float
    passed: bool
    reason: str


@dataclass(frozen=True)
class BaselineVerdict:
    """Aggregate gate outcome over every rubric."""

    passed: bool
    categories: list[CategoryVerdict]
    summary: str
    failure_lines: list[str] = field(default_factory=list)


def load_baseline(path: Path) -> dict[str, float]:
    """Read ``.eval_baseline.json`` into a ``{rubric: rate}`` dict.

    A missing baseline file returns an empty dict — the comparator treats
    that as a fresh-baseline run (no regression possible against unknown
    history) and falls back to absolute thresholds only.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    rubrics = raw.get("rubrics") if isinstance(raw, dict) else None
    if not isinstance(rubrics, dict):
        raise ValueError(
            f"{path}: expected top-level 'rubrics' object, got {type(rubrics).__name__}"
        )
    out: dict[str, float] = {}
    for name, value in rubrics.items():
        if not isinstance(value, (int, float)):
            raise ValueError(f"{path}: rubric '{name}' rate must be numeric")
        out[name] = float(value)
    return out


def write_baseline(path: Path, rates: dict[str, float], notes: str = "") -> None:
    """Persist a ``{rubric: rate}`` dict to ``.eval_baseline.json``.

    The on-disk format is stable: a single top-level ``rubrics`` object plus
    an optional ``notes`` string. Tests assert this shape so changes to the
    persistence format are intentional.
    """
    payload: dict[str, Any] = {"rubrics": {name: float(rates.get(name, 0.0)) for name in RUBRIC_NAMES}}
    if notes:
        payload["notes"] = notes
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def detect_regression(
    current_rates: dict[str, float],
    baseline_rates: dict[str, float],
    *,
    thresholds: dict[str, float] | None = None,
    max_drop: float = MAX_BASELINE_DROP,
) -> BaselineVerdict:
    """Compute per-rubric verdicts and an overall pass/fail.

    ``thresholds`` defaults to ``GATE_THRESHOLDS_W2``; tests override it to
    pin specific cases. A rubric not present in ``baseline_rates`` is
    compared only against the absolute floor — the drop check is skipped
    (no historical rate to drop from).
    """
    thresholds = thresholds if thresholds is not None else GATE_THRESHOLDS_W2

    categories: list[CategoryVerdict] = []
    failure_lines: list[str] = []

    for name in RUBRIC_NAMES:
        current = float(current_rates.get(name, 0.0))
        baseline = float(baseline_rates.get(name, 0.0)) if name in baseline_rates else None
        floor = float(thresholds.get(name, 0.0))

        below_floor = current < floor
        regressed = (
            baseline is not None and (baseline - current) > max_drop
        )
        passed = not (below_floor or regressed)

        reason_parts: list[str] = []
        if below_floor:
            reason_parts.append(
                f"{current * 100:.1f}% below floor {floor * 100:.0f}%"
            )
        if regressed and baseline is not None:
            reason_parts.append(
                f"dropped {(baseline - current) * 100:.1f}% from baseline "
                f"{baseline * 100:.1f}% (max drop {max_drop * 100:.0f}%)"
            )
        if not reason_parts:
            if baseline is None:
                reason_parts.append(
                    f"{current * 100:.1f}% (≥ floor {floor * 100:.0f}%; no baseline)"
                )
            else:
                reason_parts.append(
                    f"{current * 100:.1f}% (baseline {baseline * 100:.1f}%; "
                    f"≥ floor {floor * 100:.0f}%)"
                )

        verdict = CategoryVerdict(
            name=name,
            current_rate=current,
            baseline_rate=baseline if baseline is not None else 0.0,
            threshold=floor,
            max_drop=max_drop,
            passed=passed,
            reason=", ".join(reason_parts),
        )
        categories.append(verdict)
        if not passed:
            failure_lines.append(f"{name}: {verdict.reason}")

    overall_passed = all(c.passed for c in categories)
    summary = _summary_for(categories, overall_passed)
    return BaselineVerdict(
        passed=overall_passed,
        categories=categories,
        summary=summary,
        failure_lines=failure_lines,
    )


def render_report(verdict: BaselineVerdict) -> str:
    """One-shot text report for the pre-push hook."""
    lines = [verdict.summary, ""]
    for cat in verdict.categories:
        mark = "PASS" if cat.passed else "FAIL"
        lines.append(f"  [{mark}] {cat.name}: {cat.reason}")
    if verdict.failure_lines:
        lines.append("")
        lines.append("Failures:")
        for fl in verdict.failure_lines:
            lines.append(f"  - {fl}")
    return "\n".join(lines)


def _summary_for(categories: list[CategoryVerdict], passed: bool) -> str:
    failed = [c.name for c in categories if not c.passed]
    if passed:
        return f"W2 eval gate: PASSED ({len(categories)} categories OK)"
    return (
        f"W2 eval gate: FAILED ({len(failed)}/{len(categories)} categories regressed: "
        + ", ".join(failed) + ")"
    )
