"""Adversarial tier — pre-release. Prompt injection + auth-escape + data-quality."""

from __future__ import annotations

import os

import pytest

from copilot.eval import run_case
from copilot.eval.case import Case


pytestmark = [
    pytest.mark.adversarial,
    pytest.mark.skipif(
        not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")),
        reason="requires an LLM API key",
    ),
]


async def test_adversarial_case(adversarial_case: Case, settings, langfuse) -> None:
    result = await run_case(adversarial_case, settings=settings, langfuse=langfuse)

    # PID leaks are a hard failure regardless of any other check — release blocker.
    leaks = result.scores.get("pid_leak", {})
    if leaks.get("count", 0) > 0:
        pytest.fail(
            f"PID LEAK (release blocker) on {adversarial_case.id}: {leaks.get('leaks')}\n"
            f"Response:\n{result.response_text}"
        )

    if not result.passed:
        details = "\n  - ".join(result.failures or ["(no specific failures recorded)"])
        pytest.fail(
            f"\n{result.summary_line()}\n"
            f"Response:\n{result.response_text}\n\n"
            f"Failures:\n  - {details}"
        )
