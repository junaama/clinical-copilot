"""W-1 / W-2 / W-3 / W-4 / W-5 / W-9 / W-10 / W-11 synthesis-prompt selector — issues 006 and 007.

The classifier emits an advisory ``workflow_id``. After tool execution the
synthesis prompt selector picks framing tuned to the workflow:

* ``W-1`` (panel triage) — emphasizes ranking and "see X first".
* ``W-2`` (per-patient 24h brief) — emphasizes "what changed" overnight.
* ``W-3`` (pager-driven acute) — emphasizes acuity / next-90-seconds.
* ``W-4`` (cross-cover onboarding) — hospital-course orientation for a
  patient the clinician hasn't met.
* ``W-5`` (family-meeting prep) — diagnosis / trajectory / plan /
  prognosis surface for a family conversation. Same data shape as W-4
  via the shared ``run_cross_cover_onboarding`` composite.
* ``W-9`` (re-consult / what changed since I last looked) — diff
  framing scoped to a user-supplied ``since`` cutoff.
* ``W-10`` (panel med-safety scan) — emphasizes pharmacist-style review
  with the renal/hepatic/anticoagulant lens.
* ``W-11`` (antibiotic stewardship) — single-patient abx review, lens on
  the active abx + culture + WBC + duration / de-escalation.
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


def test_w1_prompt_includes_panel_triage_framing() -> None:
    """W-1 framing is panel-level prioritization. Marker phrase has to be
    unique enough that we can assert it isn't present for other workflows."""
    prompt = _build("W-1")
    assert "W-1 SYNTHESIS" in prompt
    lowered = prompt.lower()
    # The W-1 framing should reference ranking / prioritization language
    # and the panel-triage composite tool.
    assert (
        "rank" in lowered
        or "first" in lowered
        or "prioritiz" in lowered
    )
    assert "run_panel_triage" in prompt


def test_w1_prompt_does_not_include_w2_or_w3_framing() -> None:
    """The selector must not double-include framings."""
    prompt = _build("W-1")
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt


def test_w2_prompt_does_not_include_w1_framing() -> None:
    prompt = _build("W-2")
    assert "W-1 SYNTHESIS" not in prompt


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


def test_w4_prompt_includes_cross_cover_framing() -> None:
    """W-4 framing is hospital-course orientation for a patient the
    clinician hasn't met. Marker phrases have to be unique enough that
    we can assert it isn't present for other workflows."""
    prompt = _build("W-4")
    assert "W-4 SYNTHESIS" in prompt
    lowered = prompt.lower()
    # The W-4 framing should reference the cross-cover / orientation
    # discipline and the hospital-course narrative.
    assert "cross-cover" in lowered or "cross cover" in lowered or "shift" in lowered
    assert "course" in lowered or "admission" in lowered or "story" in lowered
    # And it points at the composite tool so the LLM picks it.
    assert "run_cross_cover_onboarding" in prompt


def test_w5_prompt_includes_family_meeting_framing() -> None:
    """W-5 framing is family-meeting prep — diagnosis, trajectory, plan,
    prognosis. Marker phrases have to be unique enough that we can assert
    it isn't present for other workflows."""
    prompt = _build("W-5")
    assert "W-5 SYNTHESIS" in prompt
    lowered = prompt.lower()
    # The W-5 framing should reference the family / diagnosis-story shape.
    assert "family" in lowered
    assert (
        "diagnosis" in lowered
        or "prognosis" in lowered
        or "trajectory" in lowered
        or "goals-of-care" in lowered
        or "code status" in lowered
    )
    # Same composite as W-4 — the framing differs, the data shape does not.
    assert "run_cross_cover_onboarding" in prompt


def test_w4_prompt_does_not_include_w5_framing() -> None:
    """W-4 (cross-cover) and W-5 (family-meeting) share a composite tool
    but their framings differ — the selector must not bleed one into the
    other."""
    prompt = _build("W-4")
    assert "W-5 SYNTHESIS" not in prompt


def test_w5_prompt_does_not_include_w4_framing() -> None:
    prompt = _build("W-5")
    assert "W-4 SYNTHESIS" not in prompt


def test_w4_prompt_does_not_include_other_framings() -> None:
    prompt = _build("W-4")
    assert "W-1 SYNTHESIS" not in prompt
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt
    assert "W-9 SYNTHESIS" not in prompt
    assert "W-10 SYNTHESIS" not in prompt
    assert "W-11 SYNTHESIS" not in prompt


def test_w5_prompt_does_not_include_other_framings() -> None:
    prompt = _build("W-5")
    assert "W-1 SYNTHESIS" not in prompt
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt
    assert "W-9 SYNTHESIS" not in prompt
    assert "W-10 SYNTHESIS" not in prompt
    assert "W-11 SYNTHESIS" not in prompt


def test_w9_prompt_includes_recent_changes_framing() -> None:
    """W-9 framing is "what changed since I last looked" — diff over a
    user-supplied cutoff. Marker phrases have to be unique enough that
    we can assert it isn't present for other workflows."""
    prompt = _build("W-9")
    assert "W-9 SYNTHESIS" in prompt
    lowered = prompt.lower()
    # The W-9 framing should reference the diff / change-since semantics.
    assert "since" in lowered
    assert (
        "chang" in lowered
        or "diff" in lowered
        or "new" in lowered
    )
    # And it points at the composite tool so the LLM picks it.
    assert "run_recent_changes" in prompt


def test_w9_prompt_does_not_include_other_framings() -> None:
    """The selector must not double-include framings."""
    prompt = _build("W-9")
    assert "W-1 SYNTHESIS" not in prompt
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt
    assert "W-4 SYNTHESIS" not in prompt
    assert "W-5 SYNTHESIS" not in prompt
    assert "W-10 SYNTHESIS" not in prompt
    assert "W-11 SYNTHESIS" not in prompt


def test_w2_prompt_does_not_include_w9_framing() -> None:
    """W-2 (24h brief) and W-9 (diff since cutoff) are easy to confuse;
    the selector must keep them distinct."""
    prompt = _build("W-2")
    assert "W-9 SYNTHESIS" not in prompt


def test_w10_prompt_includes_med_safety_framing() -> None:
    """W-10 framing is panel-level pharmacist review. Marker phrases have to
    be unique enough that we can assert it isn't present for other
    workflows."""
    prompt = _build("W-10")
    assert "W-10 SYNTHESIS" in prompt
    lowered = prompt.lower()
    # The W-10 framing should reference the pharmacist / med-safety
    # discipline and the renal/hepatic lens.
    assert (
        "med-safety" in lowered
        or "pharmacist" in lowered
        or "medication-safety" in lowered
    )
    assert "renal" in lowered or "hepatic" in lowered or "creatinine" in lowered
    # And it points at the composite tool so the LLM picks it.
    assert "run_panel_med_safety" in prompt


def test_w10_prompt_does_not_include_other_framings() -> None:
    """The selector must not double-include framings."""
    prompt = _build("W-10")
    assert "W-1 SYNTHESIS" not in prompt
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt
    assert "W-11 SYNTHESIS" not in prompt


def test_w1_prompt_does_not_include_w10_framing() -> None:
    """W-1 (panel triage) and W-10 (panel med-safety) both span the panel
    but their framings differ — the selector must not bleed one into the
    other."""
    prompt = _build("W-1")
    assert "W-10 SYNTHESIS" not in prompt


def test_w11_prompt_includes_abx_stewardship_framing() -> None:
    """W-11 framing is single-patient antibiotic stewardship — review the
    active abx alongside cultures and WBC trends. Marker phrases have to
    be unique enough that we can assert it isn't present for other
    workflows."""
    prompt = _build("W-11")
    assert "W-11 SYNTHESIS" in prompt
    lowered = prompt.lower()
    # The W-11 framing should reference antibiotic / stewardship vocab
    # and the culture / WBC / duration lens.
    assert (
        "antibiotic" in lowered
        or "stewardship" in lowered
        or "broad-spectrum" in lowered
    )
    assert (
        "culture" in lowered
        or "wbc" in lowered
        or "de-escalat" in lowered
        or "duration" in lowered
    )
    # And it points at the composite tool so the LLM picks it.
    assert "run_abx_stewardship" in prompt


def test_w11_prompt_does_not_include_other_framings() -> None:
    """The selector must not double-include framings."""
    prompt = _build("W-11")
    assert "W-1 SYNTHESIS" not in prompt
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt
    assert "W-4 SYNTHESIS" not in prompt
    assert "W-5 SYNTHESIS" not in prompt
    assert "W-9 SYNTHESIS" not in prompt
    assert "W-10 SYNTHESIS" not in prompt


def test_w10_prompt_does_not_include_w11_framing() -> None:
    """W-10 (panel med-safety) and W-11 (single-patient abx stewardship)
    are easy to confuse — both apply a med-safety lens — but their scope
    differs (panel-wide vs single-pid). The selector must keep them
    distinct."""
    prompt = _build("W-10")
    assert "W-11 SYNTHESIS" not in prompt


def test_w7_prompt_uses_default_framing() -> None:
    """A workflow without a dedicated framing falls through cleanly: no
    W-1, W-2, W-3, W-4, W-5, W-9, W-10, or W-11 framing appears in the
    prompt."""
    prompt = _build("W-7")
    assert "W-1 SYNTHESIS" not in prompt
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt
    assert "W-4 SYNTHESIS" not in prompt
    assert "W-5 SYNTHESIS" not in prompt
    assert "W-9 SYNTHESIS" not in prompt
    assert "W-10 SYNTHESIS" not in prompt
    assert "W-11 SYNTHESIS" not in prompt


def test_unclear_workflow_uses_default_framing() -> None:
    prompt = _build("unclear", confidence=0.5)
    assert "W-1 SYNTHESIS" not in prompt
    assert "W-2 SYNTHESIS" not in prompt
    assert "W-3 SYNTHESIS" not in prompt
    assert "W-4 SYNTHESIS" not in prompt
    assert "W-5 SYNTHESIS" not in prompt
    assert "W-9 SYNTHESIS" not in prompt
    assert "W-10 SYNTHESIS" not in prompt
    assert "W-11 SYNTHESIS" not in prompt


def test_default_framing_preserves_hard_rules() -> None:
    """The selector must not strip the citation / sentinel / refusal rules —
    those apply to every workflow."""
    prompt = _build("W-7")
    assert "HARD RULES" in prompt
    assert "<cite ref" in prompt
    assert "<patient-text" in prompt
