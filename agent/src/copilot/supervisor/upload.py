"""File-upload system-message helper (issue 009).

When the UI uploads a document on the user's behalf, the agent backend
injects a structured system message into conversation state so the
classifier sees it on the next turn and routes to ``W-DOC``. The format
is documented in W2_ARCHITECTURE.md §4 and copied verbatim here so the
classifier prompt can reliably pattern-match it.

The function returns a plain ``SystemMessage`` so callers can append it
to ``state["messages"]`` via the standard LangGraph reducer; no graph
state mutation happens here.
"""

from __future__ import annotations

from langchain_core.messages import SystemMessage

# Allowed doc_type values match the extraction-pipeline schemas (issue
# 002 / 004). New types must be added here AND to the classifier prompt
# enumeration in ``prompts.py`` so the classifier's instructions stay
# in sync.
_ALLOWED_DOC_TYPES: frozenset[str] = frozenset({"lab_pdf", "intake_form"})


def build_document_upload_message(
    *,
    doc_type: str,
    filename: str,
    document_id: str,
    patient_id: str,
) -> SystemMessage:
    """Build the ``[system] Document uploaded …`` message for a fresh upload.

    The message is what the classifier reads on the *next* user turn (or
    immediately, if the upload endpoint kicks off a synthetic chat call).
    Format is exactly what W2_ARCHITECTURE.md §4 and the classifier
    prompt expect — single line, sentinel prefix, structured ids in the
    body — so the W-DOC route fires reliably.

    Validation:
    * ``doc_type`` must be one of ``lab_pdf`` / ``intake_form``.
    * ``filename`` is rendered literally; callers should sanitize before
      passing (the upload endpoint already validates magic bytes and
      size, so the filename here is informational only).
    * ``document_id`` and ``patient_id`` are passed through; both are
      OpenEMR-side identifiers and not PHI on their own.
    """
    if doc_type not in _ALLOWED_DOC_TYPES:
        raise ValueError(
            f"unknown doc_type {doc_type!r}; expected one of "
            f"{sorted(_ALLOWED_DOC_TYPES)}"
        )
    content = (
        f"[system] Document uploaded: {doc_type} \"{filename}\" "
        f"(document_id: {document_id}) for {patient_id}"
    )
    return SystemMessage(content=content)
