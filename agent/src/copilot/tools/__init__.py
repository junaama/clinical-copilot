"""Tools package — re-exports ``make_tools`` and context variable helpers.

All existing imports continue to work:

    from copilot.tools import make_tools
    from copilot.tools import set_active_smart_token, get_active_smart_token
    from copilot.tools import set_active_user_id, get_active_user_id
    from copilot.tools import set_active_registry, get_active_registry
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ..care_team import CareTeamGate
from ..config import Settings
from ..fhir import FhirClient
from .composite import make_composite_tools
from .granular import make_granular_tools
from .helpers import (
    get_active_registry,
    get_active_smart_token,
    get_active_user_id,
    set_active_registry,
    set_active_smart_token,
    set_active_user_id,
)

__all__ = [
    "get_active_registry",
    "get_active_smart_token",
    "get_active_user_id",
    "make_tools",
    "set_active_registry",
    "set_active_smart_token",
    "set_active_user_id",
]


def make_tools(settings: Settings) -> list[StructuredTool]:
    """Build the full tool set bound to a shared FHIR client and CareTeam gate."""
    client = FhirClient(settings)
    gate = CareTeamGate(client, admin_user_ids=frozenset(settings.admin_user_ids))

    granular_tools, callables = make_granular_tools(settings, client, gate)
    composite_tools = make_composite_tools(gate, callables)

    return granular_tools + composite_tools
