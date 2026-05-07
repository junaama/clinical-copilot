# Chat API contract

The wire contract between the Co-Pilot UI (`copilot-ui/`) and the agent backend (`agent/src/copilot/server.py`). Both sides must conform to this document. Drift in either side is a bug.

## Endpoint

`POST /chat` on the agent service. Content-type `application/json`.

## Request

```jsonc
{
  "conversation_id": "string, non-empty, opaque to backend after persistence",
  "patient_id":      "string, FHIR Patient/<id> tail (no resource prefix)",
  "user_id":         "string, OpenEMR user identifier",
  "message":         "string, the user's typed or chip-clicked utterance",
  "smart_access_token": "string, Bearer token from the SMART launch; empty in dev/fixture mode"
}
```

The frontend sends `message` for both free-text and chip clicks (chip click sends the chip's literal label as `message`). The backend's classifier decides the workflow.

## Response

```jsonc
{
  "conversation_id": "echoes request",
  "reply": "string — the lead sentence; required for ALL blocks; UI uses this for the typewriter render",
  "block": { /* one of the block variants below; never null */ },
  "state": {
    "patient_id":  "string | null",
    "workflow_id": "W-1 | W-2 | unclear | ...",
    "classifier_confidence": "number 0..1",
    "message_count": "integer",
    "route": {
      "kind":  "chart | panel | guideline | document | clarify | refusal",
      "label": "string — user-facing route description (UI renders verbatim)"
    }
  }
}
```

`state.route` is the structured route-transparency contract from issue 039. The frontend uses `kind` to dispatch on (badge styling, header copy switch) and renders `label` verbatim — never derives the label from the kind on the client side, the backend owns the copy. The chat header surfaces the latest route label so a panel or guideline answer is not mislabeled as "Reading this patient's record".

`reply` is duplicated as `block.lead` for variants that have a lead. Frontend reads `block.lead` for the typewriter; `reply` exists for clients that only want plain text (logging, smoke tests).

## Block variants

The backend emits **exactly one** of the following block shapes per response. The UI dispatches on `block.kind`.

### `kind: "triage"` — UC-1, "who needs attention first?"

```jsonc
{
  "kind": "triage",
  "lead": "string — sentence summary, e.g. '3 of your 5 patients have something new since 22:00.'",
  "cohort": [
    {
      "id":       "string, opaque (FHIR Patient resource id when available)",
      "name":     "string",
      "age":      0,
      "room":     "string, e.g. 'MS-412'",
      "score":    75,
      "trend":    "up | down | flat",
      "reasons":  ["string", "..."],
      "self":     true,
      "fhir_ref": "Patient/<id> | null"
    }
  ],
  "citations": [ /* see Citation */ ],
  "followups": ["string", "..."]
}
```

- `cohort` is **ranked** — index 0 is highest priority.
- `score` is a 0–100 NEWS2-derived integer; UI thresholds: ≥75 high (red), ≥50 med (amber), <50 low (green).
- `self: true` marks the patient currently in scope (the SMART launch patient). UI styles this row with the accent treatment.

### `kind: "overnight"` — UC-2, "what happened to this patient overnight?"

```jsonc
{
  "kind": "overnight",
  "lead": "string — sentence summary",
  "deltas": [
    {
      "label": "string, e.g. 'Tmax', 'HR', 'SpO₂', 'Pain (R forearm)'",
      "from":  "string, formatted value with units",
      "to":    "string, formatted value with units",
      "dir":   "up | down | flat"
    }
  ],
  "timeline": [
    {
      "t":        "string — '03:14' wall-clock OR ISO 8601; UI tolerates both",
      "kind":     "Lab | Order | Med admin | Nursing note | Imaging | Vital | Other",
      "text":     "string",
      "fhir_ref": "ResourceType/<id> | null"
    }
  ],
  "citations": [ /* see Citation */ ],
  "followups": ["string", "..."]
}
```

- `timeline` is **chronological ascending** (earliest first).
- `deltas` is a small (4–6) summary set of the most clinically significant trends; UI renders as a 2-column grid.

### `kind: "plain"` — fallback for clarify / refusal / out-of-scope

```jsonc
{
  "kind": "plain",
  "lead": "string — full agent reply (no separate body)",
  "citations": [ /* optional, may be empty */ ],
  "followups": ["string", "..."]
}
```

Used for:
- Classifier confidence < threshold → clarifying question.
- Verifier refused after 2 regenerations → explicit refusal text per ARCHITECTURE.md §13.
- Free-text questions outside the W-1/W-2 scope.
- Any tool failure surfaced to the user (per §16, no silent retries).

## Citation

```jsonc
{
  "card":     "vitals | labs | medications | problems | allergies | prescriptions | encounters | documents",
  "label":    "string — human-readable, e.g. 'Vitals · last 4 readings'",
  "fhir_ref": "ResourceType/<id> | null — the resource the verifier ratified"
}
```

- `card` is the OpenEMR chart-card the citation points at. The frontend uses this to send a `postMessage` to the parent OpenEMR window asking it to flash that card. The set is closed; backend MUST emit one of the listed values or `"other"`.
- `fhir_ref` is the verifier-ratified FHIR resource per ARCHITECTURE.md §9 step 10 and §13. May be null only for `card: "other"` synthetic cites (e.g., counted-set citations from the W-1 stage-1 probe).

## Followups

`followups` is an optional array of suggested next-utterance strings. The UI renders them as ghost chips below the agent message; clicking a chip sends the string as the next `message`. Backend may emit canonical strings (e.g. "Draft an SBAR for this patient") and the classifier will route them like any other free-text message.

## Errors

HTTP status codes:

| Code | Meaning | Body |
|---|---|---|
| 200 | Successful response (including refusals — refusal is `kind: "plain"`, not 4xx) | full ChatResponse |
| 400 | Malformed request body | `{detail: "..."}` |
| 401 | Missing/invalid SMART access token when `USE_FIXTURE_FHIR=False` | `{detail: "..."}` |
| 403 | Patient-context mismatch detected at the API layer (defense in depth above the tool layer) | `{detail: "patient_context_mismatch"}` |
| 500 | Unexpected graph failure (no messages returned, etc.) | `{detail: "..."}` |
| 502 | Upstream FHIR or LLM provider failure surfaced | `{detail: "..."}` |

**Tool-level patient-context mismatches** (per ARCHITECTURE.md §7) are not 403s; they're caught inside the graph, fed back to the LLM, and surfaced as `kind: "plain"` with the audit decision `denied_authz`.

## Streaming

Out of scope for v1. Responses are turn-batched. The UI's "streaming feel" is a typewriter animation on `block.lead`; the rest of the block paints in once the response lands. Mirrors ARCHITECTURE.md §16 ("verification step requires a complete response to check, so output is turn-batched").

## Examples

### Request — clinician opens chart, asks the panel chip

```json
{
  "conversation_id": "demo-1",
  "patient_id": "4",
  "user_id": "naama",
  "smart_access_token": "Bearer eyJ...",
  "message": "Who needs attention first?"
}
```

### Response — `triage` block (abridged)

```json
{
  "conversation_id": "demo-1",
  "reply": "3 of your 5 patients have something new since 22:00. Wade235 is the highest-priority by a wide margin — possible wound infection with a rising NEWS2.",
  "block": {
    "kind": "triage",
    "lead": "3 of your 5 patients have something new since 22:00. Wade235 is the highest-priority by a wide margin — possible wound infection with a rising NEWS2.",
    "cohort": [
      {
        "id": "p1", "name": "Wade235 Bednar518", "age": 33, "room": "MS-412",
        "score": 86, "trend": "up", "self": true,
        "fhir_ref": "Patient/4",
        "reasons": [
          "NEWS2 +3 since 22:00 (HR↑, T↑, SpO₂↓)",
          "WBC 14.8, CRP 86, lactate 2.4 — possible wound infection",
          "Burn dressing not changed in 26 h"
        ]
      }
    ],
    "citations": [
      { "card": "vitals", "label": "Vitals · last 4 readings", "fhir_ref": "Observation/vital-789" },
      { "card": "labs",   "label": "Labs · 04:30",             "fhir_ref": "DiagnosticReport/lab-123" }
    ],
    "followups": ["Draft an SBAR for Wade235", "Sort cohort by NEWS2 instead", "Open Maritza Calderón"]
  },
  "state": {
    "patient_id": "4",
    "workflow_id": "W-1",
    "classifier_confidence": 0.93,
    "message_count": 2
  }
}
```

## Compatibility note

The current `agent/src/copilot/server.py:ChatResponse` returns `reply: str` only. Adopting this contract is a breaking change to the wire format. The frontend will not be built against the old shape — backend stream owns the migration.

## Owners

- Frontend: `copilot-ui/src/api/types.ts` mirrors this exactly.
- Backend: `agent/src/copilot/api/schemas.py` (new) is the source of truth for runtime; updates here trigger a frontend type regen.
