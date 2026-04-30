"""Wire-format DTOs for the chat API.

The schemas mirror ``agentforge-docs/CHAT-API-CONTRACT.md`` byte-for-byte and
are the source of truth for the runtime. Any drift between this module, the
contract document, and the frontend type mirror (``copilot-ui/src/api/types.ts``)
is a bug.
"""

from __future__ import annotations

from .schemas import (
    Block,
    ChatRequest,
    ChatResponse,
    Citation,
    CitationCard,
    CohortPatient,
    Delta,
    OvernightBlock,
    PlainBlock,
    TimelineEvent,
    TriageBlock,
    fhir_ref_to_card,
)

__all__ = [
    "Block",
    "ChatRequest",
    "ChatResponse",
    "Citation",
    "CitationCard",
    "CohortPatient",
    "Delta",
    "OvernightBlock",
    "PlainBlock",
    "TimelineEvent",
    "TriageBlock",
    "fhir_ref_to_card",
]
