"""Smoke tier — every PR. Must pass in <60s total."""

from __future__ import annotations

import os

import pytest

from copilot.eval import run_case
from copilot.eval.case import Case

from .conftest import record_case_result

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")),
        reason="requires an LLM API key (OPENAI_API_KEY or ANTHROPIC_API_KEY)",
    ),
]


async def test_smoke_case(smoke_case: Case, settings, langfuse) -> None:
    result = await run_case(smoke_case, settings=settings, langfuse=langfuse)
    record_case_result(result)
    if not result.passed:
        details = "\n  - ".join(result.failures or ["(no specific failures recorded)"])
        pytest.fail(
            f"\n{result.summary_line()}\n"
            f"Response:\n{result.response_text}\n\n"
            f"Failures:\n  - {details}"
        )
