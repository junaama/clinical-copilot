"""Adversarial tier — pre-release. Prompt injection + auth-escape + data-quality."""

from __future__ import annotations

import os

import pytest
from conftest import record_case_result  # type: ignore[import-not-found]

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
    record_case_result(result)

    # ``release_blocker: true`` cases (issue 017) carry a distinct prefix in
    # the failure output so they stand out against quality failures in the
    # pytest summary. Tier-level gating happens in conftest.pytest_sessionfinish.
    blocker_prefix = "[release-blocker] " if adversarial_case.release_blocker else ""

    # PID leaks are a hard failure regardless of any other check — always
    # treated as a release blocker for adversarial cases.
    leaks = result.scores.get("pid_leak", {})
    if leaks.get("count", 0) > 0:
        pytest.fail(
            f"{blocker_prefix}PID LEAK on {adversarial_case.id}: {leaks.get('leaks')}\n"
            f"Response:\n{result.response_text}"
        )

    if not result.passed:
        details = "\n  - ".join(result.failures or ["(no specific failures recorded)"])
        pytest.fail(
            f"\n{blocker_prefix}{result.summary_line()}\n"
            f"Response:\n{result.response_text}\n\n"
            f"Failures:\n  - {details}"
        )
