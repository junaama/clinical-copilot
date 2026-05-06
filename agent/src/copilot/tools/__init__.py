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
from .extraction import make_extraction_tools
from .granular import make_granular_tools
from .helpers import (
    get_active_registry,
    get_active_smart_token,
    get_active_user_id,
    set_active_registry,
    set_active_smart_token,
    set_active_user_id,
)
from .retrieval import make_retrieval_tools

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
    retrieval_tools = make_retrieval_tools(settings)

    extraction_tools = _build_extraction_tools(settings, client, gate)

    return granular_tools + composite_tools + retrieval_tools + extraction_tools


def _build_extraction_tools(
    settings: Settings,
    fhir_client: FhirClient,
    gate: CareTeamGate,
) -> list[StructuredTool]:
    """Wire the document-extraction tools into ``make_tools``.

    The intake_extractor worker's allowlist names ``attach_document``,
    ``list_patient_documents``, ``extract_document``,
    ``get_patient_demographics``. Without this wiring only
    ``get_patient_demographics`` (a W1 granular tool) was registered,
    so the worker had nothing to extract with and fell back to
    fetching demographics on every upload turn.

    Returns an empty list when the required deps are unconfigured
    (no Anthropic key, no Postgres DSN) so the agent still boots and
    the W1 tools keep working — extraction simply isn't available.
    """
    try:
        from ..extraction.document_client import DocumentClient
        from ..extraction.persistence import (
            DocumentExtractionStore,
            IntakePersister,
        )
        from ..llm import build_vision_model
        from ..standard_api_client import StandardApiClient
    except ImportError as exc:  # pragma: no cover - install-time guard
        import logging
        logging.getLogger(__name__).warning(
            "extraction tools unavailable: %s", exc
        )
        return []

    if not settings.anthropic_api_key.get_secret_value():
        import logging
        logging.getLogger(__name__).warning(
            "extraction tools disabled: ANTHROPIC_API_KEY not set"
        )
        return []
    if not settings.checkpointer_dsn:
        import logging
        logging.getLogger(__name__).warning(
            "extraction tools disabled: CHECKPOINTER_DSN not set"
        )
        return []

    try:
        vlm_model = build_vision_model(settings)
    except Exception as exc:  # pragma: no cover - boot-time guard
        import logging
        logging.getLogger(__name__).warning(
            "extraction tools disabled: vision model init failed: %s", exc
        )
        return []

    document_client = DocumentClient(settings)
    std_client = StandardApiClient(settings)
    store = DocumentExtractionStore(settings.checkpointer_dsn)
    persister = IntakePersister(std_client=std_client, fhir_client=fhir_client)

    return make_extraction_tools(
        gate=gate,
        document_client=document_client,
        vlm_model=vlm_model,
        store=store,
        persister=persister,
        extraction_model_name=settings.vlm_model,
    )
