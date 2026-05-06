"""Document extraction package.

Modules:
    schemas         — Pydantic models for VLM extraction outputs (issue 002).
    document_client — OpenEMR Standard-API document upload/list/download (issue 003).
    bbox_matcher    — locate extracted values inside the source PDF (issue 005).

The package ``__init__`` deliberately does not eagerly import every submodule.
Each submodule is on its own dependency chain (e.g. ``bbox_matcher`` imports
``rapidfuzz``, ``document_client`` does not), and a missing transitive dep
should only break callers of the affected submodule, not the whole package.
"""

from __future__ import annotations
