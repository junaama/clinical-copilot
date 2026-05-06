## Parent PRD

`issues/w2-mvp-prd.md`

## What to build

Wire the document extraction and evidence retrieval capabilities into the existing LangGraph StateGraph via a supervisor node, two worker nodes, and an extended classifier.

**Modules:**
- `agent/src/copilot/supervisor/graph.py` — supervisor_node, conditional routing
- `agent/src/copilot/supervisor/workers.py` — intake_extractor_node, evidence_retriever_node
- `agent/src/copilot/supervisor/schemas.py` — SupervisorDecision, HandoffEvent
- Modifications to `agent/src/copilot/graph.py` — add new nodes + edges to existing StateGraph
- Modifications to `agent/src/copilot/prompts.py` — classifier gains W-DOC and W-EVD outputs

**Graph changes:**
- Classifier gains two new routing outputs: `W-DOC` (document intent) and `W-EVD` (evidence/guideline intent)
- Both route to `supervisor_node`
- Supervisor outputs structured `SupervisorDecision`: action = extract | retrieve_evidence | synthesize | clarify
- Conditional edges dispatch to `intake_extractor_node` or `evidence_retriever_node`
- Workers return results to supervisor for synthesis
- Supervisor's final synthesis goes to the existing verifier_node
- File uploads inject system message: `[system] Document uploaded: {doc_type} "{filename}" (document_id: {id}) for Patient/{uuid}`

**Verifier extension:**
- `fetched_refs` set now accepts `DocumentReference/{id}` and `guideline:{chunk_id}` in addition to existing FHIR refs
- Citation validation covers all three ref types

**Handoff logging:**
- Each supervisor decision logged as `HandoffEvent(turn_id, from_node, to_node, reasoning, timestamp, input_summary)`
- No raw PHI in handoff logs (patient referenced by ID only)

**Worker tool access:**
- intake_extractor_node: `attach_document`, `list_patient_documents`, `extract_document`, `get_patient_demographics`
- evidence_retriever_node: `retrieve_evidence`, `get_active_problems`

## Acceptance criteria

- [ ] Classifier correctly routes document intents to supervisor (tested with "analyze this lab" and upload-notification messages)
- [ ] Classifier correctly routes evidence intents to supervisor (tested with "what do guidelines say about...")
- [ ] Classifier still routes W-1 through W-11 to existing agent_node (no regression)
- [ ] Supervisor produces structured `SupervisorDecision` with action + reasoning
- [ ] intake_extractor_node calls extraction tools and returns structured result
- [ ] evidence_retriever_node calls retrieval tool and returns evidence chunks
- [ ] Supervisor synthesizes final answer with proper citations (both document and guideline ref types)
- [ ] Verifier validates document and guideline citations against `fetched_refs`
- [ ] HandoffEvent logged for every supervisor→worker dispatch
- [ ] Integration test: full flow from "analyze Eduardo's latest lab" → extraction → cited response
- [ ] Integration test: "what do guidelines say about A1C management?" → retrieval → cited evidence
- [ ] Week 1 eval cases still pass (no regression)

## Blocked by

- `issues/006-extraction-tool-end-to-end.md` (extraction tools must exist)
- `issues/008-hybrid-retriever-cohere-rerank.md` (retrieval tool must exist)

## User stories addressed

- User story 7 (guidelines question routed correctly)
- User story 9 (separate chart facts from guideline evidence)
- User story 12 (Week 1 behavior preserved)
- User story 17 (supervisor routing logged with reasoning)
