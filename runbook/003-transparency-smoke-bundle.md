# 003 — Route and Citation Transparency Smoke Bundle

A manual smoke run for the route and citation transparency workflow
introduced by issues 040–047. Mirrors the wire shape pinned by the
automated bundle so a human walk-through against the deployed (or
fixture-backed local) app produces the same nine pass/fail signals.

This runbook is paired with two automated smoke files that pin the
wire contract every PR:

* `agent/tests/test_transparency_smoke_bundle.py` — backend pytest
  bundle (16 cases) covering ACs 1, 2, 3, 4, 7, 8, 10 against the
  `/chat` endpoint stubs.
* `copilot-ui/src/__tests__/transparencySmokeBundle.test.tsx` —
  frontend vitest bundle (15 cases) covering ACs 1–10 against the
  rendered React surfaces.

Run those first to establish that the contract holds. This runbook
covers the manual sweep — the parts that can only be verified by a
clinician walking the live UI.

---

## TL;DR

1. Sign into the deployed app at
   `https://openemr-production-c5b4.up.railway.app` as `dr_smith`,
   pausing on the login surface to confirm the consent explanation
   renders (AC9).
2. From the standalone `/copilot` shell, observe the no-patient
   welcome state (AC5), then click into the seeded Chen patient row.
   Confirm no auto-brief fires (AC6).
3. Click the **Get brief on Wei Chen** pill. Watch for the chart
   route badge (AC1) and the per-claim source chips (AC2).
4. Upload the seeded lipid panel PDF; ask a follow-up question that
   forces a document-grounded answer. Confirm the document chip
   reads `<filename> · page <n>` (AC8).
5. Ask a guideline-only question with no chart context; if the corpus
   is unreachable in this environment, confirm the refusal route
   badge and corpus-bound copy (AC3). If the corpus is reachable,
   force the failure with the network-throttle override (§5.3).
6. From the empty-state panel, ask **Who needs attention first?** to
   confirm the panel triage success or the safe failure state (AC4).
7. Reload the conversation by deep-linking to `/c/<conversation-id>`
   and confirm route badges and source chips re-render verbatim
   (AC7).
8. Run the log-leak guard (§6) over the agent and UI logs captured
   during the sweep — no raw FHIR-resource bodies, no MRNs, no DOBs
   in any log line (AC10).

The full sweep takes ≈ 8 minutes wall-clock. No real PHI ever leaves
the seeded fixture cohort — every patient on `dr_smith`'s panel is
synthetic.

---

## 1. Pre-flight

### Deployed surfaces

| Surface | URL |
| --- | --- |
| OpenEMR / Co-Pilot UI | `https://openemr-production-c5b4.up.railway.app` |
| Co-Pilot agent (HTTP) | `https://copilot-agent-production-3776.up.railway.app` |
| Langfuse UI (web) | `https://langfuse-web-production-b665.up.railway.app` |

Same demo accounts and seeded patients as runbook 002 — `dr_smith` is
on the care team for fixtures p01 (Chen), p03, and p05. Use Chen for
every per-patient step in this smoke so the trace correlator in
Langfuse can group the sweep into one session.

### Fixture-backed fallback

If the deployed environment is unreachable, run the same sweep
against a local fixture-backed agent:

```
# terminal 1: backend
cd agent
uv run uvicorn copilot.server:app --reload --port 8000

# terminal 2: frontend
cd copilot-ui
npm run dev
```

Set `USE_FIXTURE_FHIR=true` in `agent/.env` so the in-memory FHIR
client serves the synthetic Eduardo Perez fixture. The acceptance
criteria do not require deployed infrastructure — every transparency
surface is observable against the fixture.

---

## 2. AC-by-AC walk-through

### 2.1 AC9 — OAuth consent explanation (login surface)

Open the login URL in a fresh browser tab. **Do not click "Log in
with OpenEMR" yet.** The consent explanation must render on the
login card *before* the OAuth handoff.

**Expect to see**, in this order:

* App heading and subtitle.
* A boxed consent section (the testid `login-consent` should match
  in DevTools) listing four workflow families:
  - chart workflows (FHIR read of patient records)
  - panel workflows (CareTeam roster reads)
  - guideline / document workflows (uploaded files + indexed corpus)
  - source-grounding (every claim links to a citation)
* An offline-access paragraph explaining that the deployment uses
  `offline_access` so a single conversation can stay open across the
  workday without forcing a re-auth mid-conversation.
* A bolded read-only sentence that calls out **no orders, no notes,
  no chart writes** and notes that any future write capability would
  require a separate confirmation flow.
* The **Log in with OpenEMR** button below all of the above.

Capture a screenshot. Then click through to authorize.

### 2.2 AC5 — no-patient welcome / composer gating

After completing the OAuth handoff, you land in the standalone shell
with the care-team panel mounted on the right. Before clicking any
patient:

**Expect to see**:

* Welcome headline does not contain "this patient".
* The **Who needs attention first?** chip is enabled (panel-capable
  context — the W-1 panel route runs without a selected patient).
* All three patient pills (**Get brief on patient**, **Get
  medications on patient**, **Overnight trends**) render but are
  visually disabled and carry the title hint **Select a patient
  first.**
* The composer placeholder reads **Ask about your panel, or pick a
  patient for chart questions…**.
* The Send button shows the same hint as a visible row beneath the
  composer until the user starts typing.

Capture a screenshot. Try clicking a disabled pill — nothing should
happen.

### 2.3 AC6 — patient selection without auto-brief

Click the row for **Wei Chen**. Watch the transcript carefully for
the next 5 seconds:

**Expect**:

* The header subtitle updates to **Reading Wei Chen's record**.
* The Welcome card swaps to the patient-focused copy.
* The three pills now read **Get brief on Wei Chen**, **Get
  medications on Wei Chen**, **Overnight trends for Wei Chen**.
* **No user message appears in the transcript** — patient selection
  no longer auto-injects a brief prompt.
* **No `/chat` request fires** (verify in the Network tab — only
  `/me` and `/panel` should be visible from the load).

If a synthetic "Give me a brief on Wei Chen." appears as a user
message, that is a regression of issue 044. File against issue 044
and stop the sweep.

### 2.4 AC1 — chart brief / chart answer with route metadata

Click the **Get brief on Wei Chen** pill.

**Expect**:

* The pill's prompt text appears in the transcript as a normal user
  turn (no "auto-asked" tag).
* While the answer streams, only the lead is visible (typewriter).
* Once the answer settles, a **route badge** appears in the agent
  bubble. For a chart brief it should read **Reading the patient
  record** with `data-route-kind="chart"` (visible in DevTools).
* The badge label is owned by the backend (not derived on the
  frontend). It must not read **Panel data unavailable** or
  **Cannot ground this answer** for a successful chart turn.

Capture a screenshot of the rendered route badge.

### 2.5 AC2 — medication follow-up with chart source chips

In the same conversation, ask: **What active medications is Wei Chen
on?**

**Expect**:

* The agent bubble carries a **Sources** row beneath the answer text
  with one chip per cited medication.
* Each chip's text reads in clinician-recognizable form (e.g.
  `metformin · 500 mg PO BID`), not the opaque
  `MedicationRequest/<id>` resource handle.
* `data-card="medications"` is set on each chip.
* Clicking a chip triggers the chart-card flash on the OpenEMR side
  (you may need to widen the iframe to see it). The
  `copilot:flash-card` `postMessage` is also visible in DevTools.

Defense-in-depth check: any medication mentioned without a chip is a
regression of issue 040 — the verifier should drop or rewrite the
claim instead of leaving it uncited.

### 2.6 AC8 — document source chips post-upload

Use the upload widget to pick the seeded lipid panel
(`example-documents/lab-results/p01-chen-lipid-panel.pdf`). Select
document type **Lab PDF**.

After extraction completes, ask: **What did the lab report say about
LDL?**

**Expect**:

* The agent answer references the document evidence in document-
  framing language ("the lab report shows…", "the upload lists…")
  rather than as automatically persisted chart truth.
* The answer carries one or more **documents** chips with labels of
  the form `<filename> · page <n>` (e.g. `p01-chen-lipid-panel.pdf
  · page 1`).
* `data-card="documents"` is set on each chip.
* Clicking a chip routes through the chart-card flash path (issue
  046 wires the documents pane as a real chart card on the OpenEMR
  side).

### 2.7 AC4 — panel triage success or safe-failure state

Open a fresh conversation by clicking the **+** in the sidebar
(without selecting a patient — or select a patient and the panel
chip is still active because the W-1 panel route runs cohort-wide).

Ask: **Who needs attention first?**

**Expect — success path**:

* Route badge reads **Reviewing your panel** with
  `data-route-kind="panel"`.
* The body renders a triage cohort table with the listed patients,
  scores, trends, and reason chips.
* The **self** row (the user's own patient if any) is visually
  distinguished by the cohort component.

**Expect — safe failure path** (if `careteam` denied or the FHIR
backend is throttled):

* Route badge reads **Panel data unavailable** with
  `data-route-kind="panel"`.
* The lead is a clean refusal: **Panel data is unavailable right
  now…**.
* No fabricated cohort table renders.
* The user-visible reply must NOT contain any of these markers:
  `careteam_denied`, `denied_authz`, `run_panel_triage`,
  `tool_failure`, `HTTP 401`, `HTTP 403`, `HTTP 500`.

Either of these is a pass for AC4. The "leak" markers in the failure
path are the explicit guard — if any of them appears in the user-
visible reply, that is a regression of issue 042.

The technical-details `<details>` element under the answer should
expose the raw decision token (e.g. `tool_failure`) when expanded —
that is the inspectable affordance for graders. Confirm it is
collapsed by default.

### 2.8 AC3 — guideline retrieval failure / no-evidence

If the live deployment has the guideline corpus reachable, test the
**no-evidence** path: ask a question that the corpus does not
cover. Example: **What does the most recent ESC guideline say about
sub-zero patient temperatures during cryoablation?**

**Expect — no evidence path**:

* The agent honestly admits the gap. The lead reads as: **I couldn't
  find guideline evidence for that question** or similar
  corpus-bound copy.
* Route badge reads **Searching guideline evidence** with
  `data-route-kind="guideline"` if the retrieval succeeded but found
  no relevant chunks (issue 041 acceptance: empty retrieval +
  honest gap admission still passes). No source chips.

If the corpus is unreachable (or you want to force the path),
network-throttle to offline for a single retry against:
`What does ADA say about A1c targets?`

**Expect — retrieval-failure path**:

* Route badge reads **Cannot ground this answer** with
  `data-route-kind="refusal"`.
* The lead reads: **I couldn't reach the clinical guideline corpus
  this turn, so I won't offer a recommendation.** or equivalent
  corpus-bound refusal copy.
* No `<cite/>` tags, no source chips, no guideline route badge.
* The user-visible reply must NOT contain any of these markers:
  `no_active_user`, `retrieval_failed`, `evidence_retriever`,
  `connectionerror`, `HTTP 4xx`, `HTTP 5xx`.

Either path is a pass for AC3. The leak markers are the regression
guard for issue 041.

### 2.9 AC7 — conversation rehydration preserves provenance

Note the conversation id from the URL bar (e.g.
`/c/d3b9a7e4-…`). Open a fresh tab and navigate to the same URL.

**Expect**:

* The transcript rebuilds with every prior user / agent turn in
  order.
* Each agent turn re-renders its **route badge** (chart badge for
  the brief turn, panel badge for the triage turn, refusal badge for
  the guideline failure turn) verbatim.
* Each cited agent turn re-renders its **source chips** with the
  same labels they carried on the original turn.
* Triage cohort rows survive — the rehydrated triage turn does not
  flatten to plain text.
* Document chips on the post-upload turn re-render with the
  `<filename> · page <n>` label, not the opaque
  `DocumentReference/<id>` handle.

If any agent turn loses its block kind (e.g. the triage cohort
flattens to a single paragraph) or its route badge, that is a
regression of issue 045.

---

## 3. Backend smoke (one command)

The backend smoke bundle is the canonical wire-contract pin. Run it
before publishing a screenshot bundle so the manual sweep is backed
by an automated check:

```bash
cd agent
uv run pytest tests/test_transparency_smoke_bundle.py -v
```

Expect 16 passes in under 1 second. Each test name maps to one of
the AC labels above. A failure here means the wire contract has
drifted under the manual sweep — investigate the failing test before
re-running the manual run.

## 4. Frontend smoke (one command)

```bash
cd copilot-ui
npx vitest run src/__tests__/transparencySmokeBundle.test.tsx
```

Expect 15 passes in under 3 seconds. Same naming convention — each
`it` block maps to one of the manual-sweep ACs. A failure here means
a UI surface has drifted under the manual sweep.

---

## 5. Forcing failure paths

Some ACs only manifest under failure conditions that the live
environment doesn't reproduce on demand. Use these overrides
sparingly (and only against a fixture-backed agent, never against
production data).

### 5.1 Force a panel-triage failure

Set `OE_USER_ID` to a user whose `careteam` row has been removed.
The W-1 worker will fall through the CareTeam gate, the supervisor
will mark the turn `tool_failure`, and the verifier (issue 042) will
swap the answer with **Panel data is unavailable right now…**. Use
this to capture the AC4 safe-failure screenshot.

### 5.2 Force a guideline retrieval failure

Set `RETRIEVAL_BACKEND=disabled` in the agent env (or, against a
local fixture-backed agent, monkey-patch `retrieve_evidence` to
return `ok: false`). The supervisor will route a guideline-intent
turn to the evidence retriever, the tool will return `ok: false`,
and the verifier (issue 041) will swap the answer with the corpus-
bound refusal copy. Use this to capture the AC3 refusal screenshot.

### 5.3 Force a chart-citation drop

Edit a fixture FHIR Bundle to remove the `MedicationRequest/m1`
entry. The chat answer will reference the medication by name, the
verifier will detect the unfetched ref, and the chip will be
dropped. Use this to confirm the issue 040 defense-in-depth path
holds (medication mentioned with no chip → verifier should narrow
or refuse).

---

## 6. PHI / chart-content leakage guard (AC10)

The smoke bundle's automated tests use synthetic placeholders only
(`Robert Hayes`, `Eduardo Perez`, `lab_results.pdf`, `BP 90/60`).
The runbook walks against the seeded `dr_smith` cohort, which is
synthetic. **Neither path should leak chart values into log lines.**

### 6.1 Backend log sweep

While running the manual sweep, tail the agent logs:

```bash
railway logs --service copilot-agent --tail
```

After each chat turn, scan the captured log lines and confirm:

* No raw FHIR resource bodies are logged. The agent should only log
  resource *handles* (`Observation/obs-bp-2`), not the resource
  fields (`valueQuantity`, `valueString`, etc.).
* No DOBs, no MRNs, no patient names appear in any log line.
* Tool-call arguments are logged with their resource handles only;
  tool-call results are summarized as ok/fail counts, not full
  payloads.

If any chart value leaks (e.g. `BP=90/60` shows up in a log line),
that is a regression of issue 022 (PHI guard) and should block the
smoke from publishing.

### 6.2 Frontend log sweep

Open DevTools → Console while running the manual sweep. Confirm:

* No `console.log` of citation payloads.
* No `console.log` of `state.diagnostics` contents.
* No FHIR resource bodies logged on `/chat` or
  `/conversations/:id/messages` responses.

The standalone shell is configured to suppress console output in
production builds; if any of the above appears, the build is using
the dev-mode logger by mistake.

### 6.3 Smoke instructions themselves

This runbook is the smoke instructions for AC10. By design, it does
not embed any synthetic-fixture chart values inline (specific BPs,
MRNs, DOBs, medication doses). The reader is asked to observe what
the deployed UI shows, not to compare against a hardcoded expected
value. That is the AC10 contract: **the smoke instructions must
avoid raw chart-content leakage in logs**.

---

## 7. Pass / fail matrix

Capture this matrix at the end of the sweep. A single `FAIL` here
blocks the submission until the corresponding per-issue test is
fixed.

| AC | Description | Result | Notes |
| --- | --- | --- | --- |
| 1 | Chart brief + route badge | | |
| 2 | Medication chips with name/dose labels | | |
| 3 | Guideline retrieval failure → refusal badge | | |
| 4 | Panel triage success or safe-failure state | | |
| 5 | No-patient welcome + composer gating | | |
| 6 | Patient pills + no auto-brief on selection | | |
| 7 | Rehydration preserves block + route + chips | | |
| 8 | Document chip reads `<filename> · page <n>` | | |
| 9 | Login consent explanation pre-handoff | | |
| 10 | No raw chart-content in agent / UI logs | | |

Backend smoke pass count: ___ / 16 expected.
Frontend smoke pass count: ___ / 15 expected.
