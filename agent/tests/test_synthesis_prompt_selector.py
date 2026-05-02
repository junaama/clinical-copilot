"""W-2 / W-3 synthesis-prompt selector — issue 006.

The classifier emits an advisory ``workflow_id``. After tool execution the
synthesis prompt selector picks framing tuned to the workflow:

* ``W-2`` (per-patient 24h brief) — emphasizes "what changed" overnight.
* ``W-3`` (pager-driven acute) — emphasizes acuity / next-90-seconds.
* anything else — falls through to the default framing.

These tests exercise the selector through ``build_system_prompt`` (the
single entry point used by the graph's agent_node) so the test runs the
exact code the production graph runs.
"""

from __future__ import annotations

from copilot.prompts import build_system_prompt


def _build(workflow_id: str, *, confidence: float = 0.95) -> str:
    return build_system_prompt(
        registry={
            "fixture-1": {
                "patient_id": "fixture-1",
                "given_name": "Eduardo",
                "family_name": "Perez",
                "birth_date": "1958-03-12",
            }
        },
        focus_pid="fixture-1",
        workflow_id=workflow_id,
        confidence=confidence,
    )


def test_w2_prompt_includes_overnight_brief_framing() -> None:
    """W-2 framing is the 24-hour-brief discipline ('what changed since last
    look'). The marker phrase has to be unique enough that we can assert it
    isn't present for other workflows."""
    prompt = _build("W-2")
    assert "W-2 SYNTHESIS" in prompt
    # The W-2 framing should reference the overnight / 24-hour focus.
    assert "24" in prompt or "overnight" in prompt.lower() or "since" in prompt.lower()


def test_w3_prompt_includes_acuity_framing() -> None:
    """W-3 framing emphasizes acuity, current threats, the next 90 seconds."""
    prompt = _build("W-3")
    assert "W-3 SYNTHESIS" in prompt
    # The W-3 framing should foreground acuity / current threat / urgency.
    lowered = prompt.lower()
    assert (
        "acuity" in lowered
        or "threat" in lowered
        or "90 seconds" in lowered
        or "urgent" in lowered
    )


def test_w2_prompt_does_not_include_w3_framing() -> None:
    """The selector must not double-include framings."""
    prompt = _build("W-2")
    assert "W-3 SYNTHESIS" not in prompt


def test_w3_prompt_does_not_include_w2_framing() -> None:
    prompt = _build("W-3")
    assert "W-2 SYNTHESIS" not in prompt


def test_w7_prompt_uses_default_framing() -> None:
    """A workflow without a dedicated framing falls through cleanly: neither
    W-2 nor W-3 framing appears in the prompt."""
    prompt = _build("W-7")
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt


def test_unclear_workflow_uses_default_framing() -> None:
    prompt = _build("unclear", confidence=0.5)
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt


def test_default_framing_preserves_hard_rules() -> None:
    """The selector must not strip the citation / sentinel / refusal rules —
    those apply to every workflow."""
    prompt = _build("W-7")
    assert "HARD RULES" in prompt
    assert "<cite ref" in prompt
    assert "<patient-text" in prompt
