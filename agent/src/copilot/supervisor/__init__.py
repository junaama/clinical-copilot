"""Supervisor + workers package (issue 009).

The Week 2 graph keeps the Week 1 path (classifier → agent → verifier)
intact and adds two new routes for document-extraction and
evidence-retrieval intents:

    classifier ──W-DOC──▶ supervisor ──extract────────▶ intake_extractor
                                    └─retrieve_evidence ▶ evidence_retriever
                                    └─synthesize        ▶ verifier
                                    └─clarify           ▶ END

Workers narrow their tool surfaces (intake_extractor sees the document
tools; evidence_retriever sees the retrieval tools) so the LLM can't
mis-pick across capability boundaries during a structured workflow.

Public surface:

    from copilot.supervisor.schemas import (
        SupervisorAction,
        SupervisorDecision,
        HandoffEvent,
    )
    from copilot.supervisor.graph import (
        build_supervisor_node,
        route_after_supervisor,
    )
    from copilot.supervisor.workers import (
        build_intake_extractor_node,
        build_evidence_retriever_node,
        WORKER_TOOL_ALLOWLIST,
    )
    from copilot.supervisor.upload import build_document_upload_message
"""

from __future__ import annotations
