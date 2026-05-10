"""``resolve_patient`` tool — issue 003.

Covers:

* Single-match resolution against the user's CareTeam roster.
* Ambiguous resolution returns multiple candidates with DOBs.
* Not-found path collapses CareTeam pre-filter with "doesn't exist".
* ``clarify`` for inputs too sparse to search.
* Cache hit on second call short-circuits the FHIR roundtrip.
"""

from __future__ import annotations

import pytest

from copilot.config import Settings
from copilot.fixtures import PRACTITIONER_DR_SMITH
from copilot.tools import (
    make_tools,
    set_active_registry,
    set_active_user_id,
)


def _settings(*, admins: tuple[str, ...] = ()) -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        USE_FIXTURE_FHIR=True,
        COPILOT_ADMIN_USER_IDS=",".join(admins),
    )


@pytest.fixture(autouse=True)
def _reset_context():
    set_active_user_id(None)
    set_active_registry({})
    yield
    set_active_user_id(None)
    set_active_registry({})


def _tool(*, admins: tuple[str, ...] = ()):
    for tool in make_tools(_settings(admins=admins)):
        if tool.name == "resolve_patient":
            return tool
    raise KeyError("resolve_patient")


async def test_resolves_single_match_against_dr_smith_panel() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": "Hayes"})

    assert result["status"] == "resolved"
    assert len(result["patients"]) == 1
    assert result["patients"][0]["patient_id"] == "fixture-3"
    assert result["patients"][0]["family_name"] == "Hayes"
    assert result["patients"][0]["birth_date"] == "1949-11-04"


async def test_resolves_by_full_name() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": "Robert Hayes"})

    assert result["status"] == "resolved"
    assert result["patients"][0]["patient_id"] == "fixture-3"


async def test_not_found_for_off_team_patient() -> None:
    """Maya Singh (fixture-2) exists but is NOT on dr_smith's CareTeam.
    The resolver collapses "exists but you don't have access" with
    "doesn't exist" — both surface as not_found."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": "Singh"})

    assert result["status"] == "not_found"
    assert result["patients"] == []


async def test_not_found_for_unknown_name() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": "Mxyzptlk"})

    assert result["status"] == "not_found"


async def test_clarify_for_too_sparse_input() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": ""})

    assert result["status"] == "clarify"
    assert result["ok"] is False


async def test_clarify_for_single_char_input() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": "H"})

    assert result["status"] == "clarify"


async def test_admin_sees_off_panel_patients() -> None:
    """Admin's panel is the full set, so resolution succeeds for anyone."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke({"name": "Singh"})

    assert result["status"] == "resolved"
    assert result["patients"][0]["patient_id"] == "fixture-2"


async def test_dob_filter_disambiguates_when_supplied() -> None:
    """If the registry/panel has multiple matches by name, dob narrows
    them. We simulate this with a single match plus a DOB hit."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": "Hayes", "dob": "1949-11-04"})

    assert result["status"] == "resolved"
    assert result["patients"][0]["patient_id"] == "fixture-3"


async def test_dob_mismatch_yields_not_found() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"name": "Hayes", "dob": "1900-01-01"})

    assert result["status"] == "not_found"


async def test_cache_hit_returns_cached_row_without_panel_fetch() -> None:
    """Second call for an already-resolved name short-circuits.

    We seed the registry contextvar directly (mimicking what the agent_node
    does between turns) and assert the resolver returns the cached row with
    sources_checked=['CareTeam (cached)'] — even with NO active user_id, so
    the fallthrough to gate.list_panel cannot have happened."""
    set_active_registry(
        {
            "fixture-3": {
                "patient_id": "fixture-3",
                "given_name": "Robert",
                "family_name": "Hayes",
                "birth_date": "1949-11-04",
            }
        }
    )
    # No user_id set — the cache must serve the result without consulting
    # the gate.
    tool = _tool()

    result = await tool.ainvoke({"name": "Hayes"})

    assert result["status"] == "resolved"
    assert result["sources_checked"] == ["CareTeam (cached)"]
    assert result["patients"][0]["patient_id"] == "fixture-3"


async def test_cache_hit_matches_display_cleaned_synthetic_name() -> None:
    """Display-cleaned names from the UI still resolve against raw roster rows."""
    set_active_registry(
        {
            "fixture-synthetic": {
                "patient_id": "fixture-synthetic",
                "given_name": "Chang742",
                "family_name": "Durgan921",
                "birth_date": "1972-03-07",
            }
        }
    )
    tool = _tool()

    result = await tool.ainvoke({"name": "Chang Durgan"})

    assert result["status"] == "resolved"
    assert result["sources_checked"] == ["CareTeam (cached)"]
    assert result["patients"][0]["patient_id"] == "fixture-synthetic"


async def test_ambiguous_when_multiple_panel_patients_share_substring() -> None:
    """Admin's panel contains all five patients. ``a`` matches multiple
    given/family names, so the resolver returns ambiguous candidates."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    # "ar" matches Eduardo, Park, Hayes, etc. — multiple
    result = await tool.ainvoke({"name": "ar"})

    assert result["status"] == "ambiguous"
    assert len(result["patients"]) > 1
    # Every candidate carries DOB so the user can disambiguate
    assert all(p.get("birth_date") for p in result["patients"])
