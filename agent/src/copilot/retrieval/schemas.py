"""Schemas for retrieval results.

Defines ``SourceCitation`` and ``EvidenceChunk`` — the return shape of the
hybrid retriever. These models are intentionally narrow: only what callers
of ``Retriever.retrieve`` need.

A parallel set of schemas covering document extraction (``LabExtraction``,
``IntakeExtraction``, ``BoundingBox``, etc.) is being introduced under
``copilot.extraction.schemas`` by issue 002. ``EvidenceChunk`` and
``SourceCitation`` are duplicated here so the retrieval module does not
depend on the extraction package being merged first; a follow-up should
unify them once both modules have landed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ``model_config = ConfigDict(extra="forbid")`` mirrors issue 002's strict
# Pydantic stance: extra fields are a bug, not a forward-compat trick.
_STRICT = ConfigDict(extra="forbid", frozen=True)


class SourceCitation(BaseModel):
    """Where a piece of evidence came from.

    ``source_type`` discriminates the citation kind so the verifier can
    validate it against the correct ref namespace:

    * ``"guideline"`` — guideline-corpus chunk; ``source_id`` is the chunk
      id; ``page_or_section`` is the section heading; ``field_or_chunk_id``
      is the chunk id again (kept for symmetry with the document case);
      ``quote_or_value`` is the chunk text or a representative excerpt.
    * ``"document"`` — patient document extraction (issue 002 territory);
      filled in by the extraction pipeline, not by the retriever.
    """

    model_config = _STRICT

    source_type: Literal["guideline", "document"]
    source_id: str
    page_or_section: str | None = None
    field_or_chunk_id: str | None = None
    quote_or_value: str | None = None


class EvidenceChunk(BaseModel):
    """One ranked chunk returned by the retriever.

    ``relevance_score`` is the rerank score when Cohere reranks the
    candidate set, or the RRF score when rerank is unavailable / fails
    open. The two are not directly comparable; callers should use it for
    in-result ordering only, not as an absolute confidence.
    """

    model_config = _STRICT

    chunk_id: str
    guideline_name: str
    section: str | None = None
    page: int | None = None
    text: str
    relevance_score: float = Field(ge=0.0)
    source_citation: SourceCitation
