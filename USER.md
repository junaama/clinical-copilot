# Clinical Co-Pilot — Target Users and Use Cases

This document defines the clinical roles the agent is built for and the specific
moments it serves. The **inpatient hospitalist** is the primary user — every
architectural decision is made first for them. Two adjacent roles (the
**consulting specialist** and the **inpatient clinical pharmacist**) are
secondary users; the same agent serves their workflows with no structural
change, only different scope and different starter prompts. Including them
here makes explicit which decisions are universal and which are
hospitalist-specific.

---

## Primary user: The Inpatient Hospitalist Starting Morning Rounds

A hospitalist physician on a busy inpatient service, picking up rounds at 7:00 AM
with **12–20 active patients** on their list. They will see every patient on that
list before noon. They are responsible for synthesizing what changed overnight,
deciding what each patient needs today, and walking out of each room with a plan.

Concrete profile:

- **Role:** Internal medicine hospitalist (attending or senior resident)
- **List size:** 12–20 admitted patients (typical adult medicine service)
- **Time budget:** ~20–30 minutes of pre-rounds prep before the first patient room
- **Familiarity:** Variable. Some patients they admitted; some were handed off from
  a colleague last night or last weekend. They cannot assume they know any patient
  cold.
- **Primary constraint:** Time. Every minute spent pre-rounding is a minute not
  spent at the bedside or charting later in the day.

### Why this user

| Archetype | Why not (for this project) |
|---|---|
| Primary care physician, 20-patient day | Each visit is a self-contained 15-min slot; less prose-heavy synthesis required between visits, more "pull up the patient's record." Less leverage for an LLM. |
| ED resident on overnight intake | Patients are unknown on arrival; the bottleneck is acute presentation, not chart synthesis. Higher legal/risk surface for AI in undifferentiated patients. |
| **Inpatient hospitalist** | **Wins because:** (a) repeat exposure to the same chart over many days, (b) overnight events to reconstruct from multiple structured + free-text sources, (c) clear pre-rounds time window where the value lands, (d) lower acuity cliff than ED, so an agent that supports rather than acts is a clean fit. |

---

## Workflow: 7:00 AM Pre-Rounds

**The 30 seconds before they open the agent.** The hospitalist sits down at a
shared workstation, pulls up their patient list in OpenEMR, and starts what would
otherwise be a manual synthesis loop: open patient → vitals tab → labs tab →
nursing notes → MAR → cross-cover physician note → imaging → close → repeat for
the next patient. **5–7 tabs × 12–20 patients ≈ 60–140 clicks** before they have
walked into a single room.

This click-flipping is the pain the agent collapses.

**What the agent must do during this window:**

- Run on the hospitalist's own list (authorization scoped to *this* clinician's
  patients only — a covering hospitalist must not see patients they are not on
  service for)
- Read across structured (vitals, MAR, labs, orders) and unstructured (nursing
  notes, physician notes, imaging reads) sources
- Surface significance, not just data — flag what *matters* for *this* patient
  given *their* history
- Ground every claim in a specific record so the hospitalist can verify in one
  click

**What happens after the agent's output.** The hospitalist either (a) walks into
the first prioritized patient's room with the right context loaded, or (b) asks
a follow-up to drill into something the brief surfaced. They do not act inside
OpenEMR through the agent; the agent is read-only.

---

## Specific Use Cases

### UC-1: Cross-patient triage — "Who do I need to see first?"

**The literal question:** *"Of my 18 patients, which ones had clinically
significant changes overnight that I should see first?"*

**Sources the agent must read:**
- Vitals (last 24 hours, with deltas vs. prior 24 hours)
- MAR — medications given, missed, refused, PRN frequency
- New labs since the last rounds
- Nursing notes (free text)
- Cross-cover / overnight physician notes (free text)
- New orders, holds, discontinuations
- Imaging results posted overnight

**Output shape:** A short ranked list (typically 3–6 patients) with one-line
justifications and a residual note that the rest of the list is unchanged or
stable. Each flagged patient links back to the source records that triggered
the flag.

**Why an agent (and specifically why not a dashboard):**
Significance is patient-specific. A 2 lb overnight weight gain is alarming for
a CHF patient and meaningless for a post-op patient on IV fluids. A dashboard
renders the same fields the same way for every patient and forces the
hospitalist to do the "is this normal for them" reasoning manually, 18 times.
An LLM grounded in each patient's history can prioritize with reasoning a
fixed-layout view cannot replicate.

**Why conversational (and specifically why this earns the chatbot shape):**
The triage answer is the *opening* of the workflow, not the end. After it, the
hospitalist drills down into specific patients (UC-2) without context-switching
back to the list. Multi-turn is justified because the second question depends
on the answer to the first.

---

### UC-2: Per-patient 24-hour brief — "What happened to this patient overnight?"

**The literal question:** *"What happened to Patient X in the last 24 hours?"*
asked as a follow-up to UC-1, or independently when the hospitalist already
knows which patient they want context on.

**Sources:** Same set as UC-1, scoped to one patient.

**Output shape:** A timestamped, chronological bullet list of events —
medications given/missed, vital sign excursions and how they were addressed,
nursing-note events (falls, agitation, interventions), lab results, orders
placed, consultant input. Each bullet cites the source record (note ID, vitals
timestamp, order ID).

**Why an agent (and specifically why not a dashboard):**
A meaningful chunk of the relevant information lives in **free-text prose**:
nursing notes describing events ("patient hypotensive at 03:14, 250 cc NS bolus,
recovered to 110/70 by 04:00"), cross-cover sign-out notes, consult
recommendations. A dashboard can render those notes as cards but cannot
reorganize their *contents* into a single chronological timeline alongside
structured data. The synthesis from prose-into-timeline is exactly what a
language model does well that a fixed UI does not.

**Why conversational:** The first follow-up after seeing the timeline is
predictable and personal: *"Tell me more about the 3 AM hypotensive episode,"*
*"What was the cardiology consult's reasoning?"* These follow-ups expect
context to carry forward. A search box or a dashboard makes the user
re-enter context every time.

---

### UC-3: Pager-driven acute context — "What's the picture on this patient, fast?"

**The literal question:** *"Patient in 7-East just dropped to 82/48. What's
their baseline, what have we already tried today, and what's the relevant
history?"*

**The 30 seconds before they open the agent.** A page or a Vocera call
interrupts whatever the hospitalist was doing. They are walking — sometimes
running — toward the room or stepping aside in a hallway with a phone to one
ear. They need orientation in the elevator, not in the chart five minutes
from now. Click-flipping is impossible; one-handed reading is the constraint.

**Sources the agent must read:**
- The last 12 hours of vitals with their distribution (so "82/48" can be
  framed as "this patient's lowest reading today" vs "this patient runs in the
  90s/50s normally")
- Active medications with timing — recent vasoactives, sedatives,
  antihypertensives, diuretics, anything that could explain the drop
- Today's nursing notes and physician notes (any prior hypotensive episode in
  this admission, any intervention already documented)
- Recent labs with directional change (lactate, hemoglobin, creatinine,
  troponin)
- Active problem list — sepsis workup in progress, GI bleed, recent
  procedure, anything that reframes a vital-sign change as expected vs
  alarming
- Code status, allergies, and current goals of care (the hospitalist must
  not call a code on a patient who is comfort-care)

**Output shape:** Three short blocks — *Baseline & today's trend*, *What's
already been tried in the last 12 hours*, *Context that reframes this number*
— totaling well under a screen. Each item cites a source. The agent does not
suggest interventions; it tells the hospitalist what the chart says so the
hospitalist can make the call at the bedside.

**Why an agent (and specifically why not a dashboard):** The pager workflow
is *event-driven*, not screen-driven. A dashboard is a place a hospitalist
*chooses* to go; a pager-driven brief has to be answered without a chosen
destination. The agent is the only shape that can take a freeform trigger
("82/48 in 7-East") and respond with the patient-specific reframing — a
dashboard cannot decide that for a comfort-care patient the number is not an
emergency. Speed-of-context is the entire feature.

**Why conversational:** The first follow-up is reliably one of *"Has this
happened before this admission?"*, *"What did we give them at 14:30?"*, or
*"What was the lactate this morning?"* — context carries forward, the
hospitalist is hands-busy, multi-turn beats re-querying.

---

### UC-4: Cross-cover onboarding — "Get me oriented on patients I've never met"

**The literal question:** *"I'm covering this service tonight. Walk me
through the four sickest patients on the list and what I should be watching
for overnight."*

**The 30 seconds before they open the agent.** A nocturnist or weekend
hospitalist is starting a shift on a service they were not on yesterday.
They have a written sign-out from the day team, a list of 14–22 patients,
and zero prior mental model. The sign-out captures the day team's concerns
— it does not capture the *arc* of any individual patient or the structured
data the day team didn't write down.

**Sources the agent must read:**
- Admission diagnosis and hospital day count for each patient
- Active problem list with onset dates within the admission
- Trajectory data — vitals trends across the admission, lab trends across
  the last 72 hours, oxygen requirement changes, mental-status notes
- The most recent attending and consultant notes (free text)
- Pending studies, pending consults, pending dispositions (e.g., "awaiting
  rehab bed since Tuesday")
- Code status and goals-of-care notes
- Day-team sign-out / handoff notes if they live in OpenEMR

**Output shape:** A ranked walkthrough (typically 3–5 patients flagged as
"watch tonight") with, for each: a one-line admission summary, the
trajectory ("trending toward extubation" vs "creatinine doubled today"),
what to watch for overnight, and what's pending. The remainder of the list
gets a one-line "stable, plan unchanged" with a citation to the most recent
attending note so the covering hospitalist can verify quickly.

**Why an agent (and specifically why not a dashboard):** The synthesis the
covering hospitalist needs is across **days of admission and across multiple
note types** — the day team's note, the consultant's note, nursing
observations — and it needs to be reorganized as *trajectory*, not as
last-known-value. A dashboard shows current state; the cross-cover need is
historical-into-prognostic. An LLM can build the arc; a fixed UI cannot.

**Why conversational:** The walkthrough is the *opening* of the shift.
Predictable follow-ups are patient-specific (*"Tell me more about the GI
bleed in bed 12"*) and disposition-specific (*"What's blocking discharge on
bed 5?"*). The covering hospitalist will ask several drill-in questions
before walking to the first room — exactly the multi-turn shape.

---

### UC-5: Family-meeting prep — "What do I need to know to talk to this family?"

**The literal question:** *"I have a family meeting in ten minutes for the
patient in bed 14. What's the arc of this admission, what's currently being
done, and what's still pending?"*

**The 30 seconds before they open the agent.** The hospitalist has a window
between rounding and the conference room. They may not be the admitting
physician; they may have inherited this patient from a colleague three days
ago. They need to answer family questions — *"How long has she been here?"*,
*"What's the plan?"*, *"Why did the antibiotic change yesterday?"* — without
flipping to the chart mid-conversation, which families read as
disorganization.

**Sources the agent must read:**
- Admission date, admitting diagnosis, and hospital course in chronological
  bullets (admit → escalations → de-escalations → today)
- Major events — transfers (ED → floor → ICU → step-down), procedures,
  consults engaged, code-status changes
- Current active treatments (antibiotics, drips, oxygen, nutrition, lines)
- Pending studies / pending consults / pending dispositions
- Goals-of-care notes and any documented family discussions to date (so the
  hospitalist does not contradict what was said yesterday)
- Code status

**Output shape:** A two-section brief — *The arc so far* (chronological,
written in plain non-jargon language since the doctor will paraphrase it to
the family) and *Where we are right now* (active treatments, pending items,
expected next 24–48 hours of decisions). Each item cites the source so the
hospitalist can stand behind any specific date or fact the family asks
about.

**Why an agent (and specifically why not a dashboard):** Family-meeting prep
needs **narrative**, not data. The hospitalist is going to *speak* this
content, not click through it. A dashboard renders the latest values; what
the family wants is a story that reconciles seven days of records into a few
paragraphs. The synthesis is the entire point — fixed UIs cannot produce a
narrative tuned to *this* admission.

**Why conversational:** The follow-ups before walking into the room are
predictable and depend on what the brief says: *"Has the family been told
about the DNR conversation from Monday?"*, *"What did Dr. Patel actually
recommend in her consult note?"*, *"Have we discussed hospice yet?"* The
conversational shape lets the hospitalist refine the brief in two or three
turns until they feel ready.

---

### UC-6: Causal trace — "Why is this happening?"

**The literal question:** *"Why has Mrs. Lopez's creatinine been climbing
this week?"* — or *"Why is his sodium dropping?"*, *"Why has her oxygen
requirement crept up?"*, *"Why is the heart rate trending in the 110s?"*

**The 30 seconds before they open the agent.** The hospitalist notices a
trend during rounds or while pre-rounding. They have ten seconds to decide
if it's something to dig into now or defer. Right now, "dig into" means
flipping through five tabs to triangulate — recent medication changes,
intake/output, lab cofactors, recent imaging with contrast, baseline from
the problem list. They can't reasonably do that mid-rounds, so they defer
the question to "after I'm done with the list." Things slip.

**Sources the agent must read:**
- Medications, with timestamps of recent starts, changes, and PRN
  administration — the usual suspects for the metric in question (ACEi/ARBs
  and NSAIDs for creatinine; diuretics and IV fluids for sodium; sedatives
  and oxygen titration for respiratory; new beta-blocker holds or fevers
  for heart rate)
- Labs with directional change over the relevant window — including
  cofactors (BUN with creatinine, urine sodium with serum sodium, lactate
  with hemodynamics)
- Intake / output documentation for volume-related questions
- Recent procedures and contrast exposure
- Active problem list for baseline (eGFR baseline, baseline sodium, known
  hypoxia, etc.)
- The most recent attending and consultant notes — physicians often
  document the hypothesis they're already considering, and the agent should
  not contradict or duplicate without acknowledging it

**Output shape:** A short hypothesis-ranked list — typically two to four
candidate explanations the chart actually contains, each with the specific
data that triggered it and a citation. Example: *"Three things in the chart
that line up with the rising creatinine: (1) lisinopril started 2026-04-25,
Cr was 1.2 the day before; (2) ibuprofen 600 mg given PRN four times since
04-26; (3) baseline CKD per problem list, eGFR 52 on admit. No contrast in
the last seven days; last urinalysis showed bland sediment."* The agent
does not diagnose. It does not pick a leading explanation. It puts the
chart's correlated data in front of the doctor so the doctor can decide
which lead to chase.

**Why an agent (and specifically why not a dashboard):** A dashboard can
show the trend that prompted the question. It cannot tell you what
*correlates with* the trend. The cognitive work the doctor was about to do
manually — pull the med list, pull the lab trend, line them up by date,
note what changed in the same window — is the agent's defining job. It is
synthesis across separately-rendered data sources, which is the one thing
fixed UIs cannot do.

**Why conversational:** After the hypothesis list, the hospitalist narrows.
*"How much ibuprofen total?"*, *"What was the BUN this morning?"*,
*"Was she over-diuresed on Monday?"*, *"Did anyone document a hypothesis
already?"* The conversation walks the doctor through the differential
without forcing them to issue a fresh well-formed query each time.

---

### UC-7: Targeted drill — "Just answer the specific question I have"

**The literal question:** A short, sharp factual question, followed by
several more in sequence, each depending on the previous. *"What was the
indication for cefepime?"* → *"What did ID say in their consult?"* → *"Has
the second set of cultures resulted yet?"* → *"What's the lactate trend
over the last 24 hours?"*

**The 30 seconds before they open the agent.** The hospitalist is on the
phone with a consultant who just asked a specific question. Or a resident
is presenting in the workroom and the attending wants to verify a fact in
the chart without taking over the keyboard. Or a nurse pages with a
specific question about a med order. Or the hospitalist is walking to a
family meeting and one detail came up they cannot pretend to know. They
need a sharp fact, fast, and they need to ask the next one without
re-orienting the system. They cannot afford to type a five-line query each
time — they think in short questions, the way a colleague would ask them.

**Sources the agent must read:** Everything in the chart, scoped to the
patient already in context. The point of this use case is not which
sources — the point is that the *patient* and the *thread of conversation*
are the scope, and any single question may pull from any source.

**Output shape:** Short, direct answers — one to three sentences each,
cited. The agent does not pre-emptively volunteer adjacent context. It
does not summarize. It does not editorialize. If the user wants more, they
ask the next question; the conversation carries the context forward.

**Why an agent (and specifically why not a dashboard):** Each individual
question could, in isolation, be answered by clicking to a specific tab.
But the workflow is *a series of fast questions where each one depends on
the answer to the previous*. *"Has the second set resulted"* is meaningless
without the *"indication for cefepime"* established a moment ago. The
hospitalist's flow is *ask → glance → ask the implied next thing*.
Conversation matches that shape natively; tab-clicking restarts orientation
on every question.

**Why conversational:** This use case earns the chatbot shape more cleanly
than any other in this document. The entire workflow is multi-turn —
single-turn does not exist here, because the second question is the point.
A search box, a dashboard, or a "smart sidebar" all force the user to
re-establish context with every query. The conversation *is* the feature.

---

## Secondary user: The Consulting Specialist

A subspecialist physician — cardiology, infectious disease, nephrology,
critical care, GI, etc. — who is **not on the primary team** and is paged
to consult on a patient they have never seen. They will see ~5 to 15
consult patients per day across multiple services. Their job is to answer
a specific consult question, document an assessment, recommend a plan, and
move on. They rarely follow the patient longitudinally unless re-consulted.

Concrete profile:

- **Role:** Subspecialty attending or senior fellow
- **Patient panel:** ~5–15 active consults per day, across multiple wards
- **Time budget:** ~5–10 minutes of chart prep before walking into the
  patient's room
- **Familiarity:** Zero baseline. They have never met this patient. They
  have a one-sentence consult reason from the page and nothing else.
- **Primary constraint:** Information density per minute. They need a
  *specialty-lens* orientation — the same chart looks different to a
  cardiologist than to a nephrologist, and a generic "patient summary"
  wastes their five minutes.

**Why agent leverage is high here.** A first-time chart visit is the
exact failure mode of dashboards: a dashboard shows you everything
equally and forces *you* to filter for what's relevant to your specialty.
A hospitalist who has been following a patient for three days has a
mental model that filters automatically; a consultant has none. The
agent collapses the orientation step from five minutes of click-flipping
across eight tabs into a one-paragraph specialty-tuned brief.

### UC-8: Consult orientation — "Why was I called and what's the chart story for my specialty?"

**The literal question:** *"Cards consult on bed 432 for new-onset AFib.
Who is this patient, why are they admitted, and what's relevant to my
consult?"* — or a nephrology consult on AKI, an ID consult on persistent
fever, a GI consult on a GI bleed.

**The 30 seconds before they open the agent.** The page just came
through. They are walking from the ICU to a different floor, or stepping
out of another consult, with the consult reason and the room number on a
sticky note. They have until they reach the patient's door to be
oriented. Right now, that means logging into OpenEMR while walking and
opening: admit H&P, recent attending notes, problem list, current meds,
recent vitals, recent labs, prior consults if any, the most recent
attending note for the chart trajectory. Eight tabs in five minutes,
one-handed if they're walking.

**Sources the agent must read:**
- Admit H&P (admission diagnosis, chief complaint, the story of why this
  patient is in the hospital)
- Active problem list with onset relative to admission
- Current medications, especially ones relevant to the consult question
  (rate-control / anticoagulants for cards, antibiotics for ID,
  diuretics / ACEi for nephrology, etc. — the specialty determines the
  filter)
- Recent vitals trend, framed by the consult question (rate trend for
  AFib consult, BP trend for hypertensive emergency, temperature curve
  for fever)
- Specialty-relevant labs (TSH + electrolytes for AFib, urine studies +
  Cr trend for AKI, lactate + cultures for sepsis, hemoglobin trend for
  GI bleed)
- Specialty-relevant studies (recent EKGs for cards, urinalysis for
  nephrology, imaging for GI bleed)
- Most recent attending and consultant notes — the primary team's
  hypothesis is part of what the consultant needs to either confirm,
  refute, or extend

**Output shape:** A focused one-paragraph brief in three parts:
*"Why they are admitted"* (one sentence), *"What's relevant to your
consult question"* (the specialty-tuned facts), *"What the primary team
has tried and is currently thinking"* (so the consultant doesn't
duplicate or contradict). Each fact carries a citation. The agent does
not recommend a workup or a treatment.

**Why an agent (and specifically why not a dashboard):** The brief is
*specialty-specific*. A cardiology brief on the same patient looks
different from a nephrology brief on that patient — different fields are
relevant, different trends matter, different prior interventions are
worth surfacing. A dashboard renders the same fields the same way for
every viewer. An LLM can take the consult reason as input and tune the
brief's content accordingly. This is the iconic agent-only capability:
filtering relevance by who is asking and why.

**Why conversational:** Specialty consultants drill, hard. Once oriented,
they ask: *"Any prior AFib history?"*, *"What's the current rate?"*,
*"Has she been on any rate control already?"*, *"Echo on file? What was
the last EF?"* Each question is a fact-finding probe whose answer
informs the consultant's recommendation. Multi-turn matches the cognitive
shape of a consult workup; the alternative (re-querying with full
context every time) loses against the tab-clicking they were already
doing.

---

### UC-9: Re-consult — "What changed since I last touched this chart?"

**The literal question:** *"I was consulted on this patient three days
ago and recommended starting rate control and getting an echo. The team
re-consulted today. What happened in between?"*

**The 30 seconds before they open the agent.** A re-consult arrived for
a patient the consultant has touched before. The chart has accumulated
three days of new data, notes, and decisions. The consultant doesn't
need a fresh full orientation — they need the *delta* since their last
note, with their own recommendations as the anchor.

**Sources the agent must read:**
- The consultant's own previous consult note(s) on this patient (the
  anchor — what did *I* recommend last time?)
- Everything the primary team has done since that note: orders placed,
  meds started or held, studies completed, results available
- Specifically, whether the consultant's prior recommendations were
  followed, declined, or modified
- New events relevant to the consult issue (new labs, new vitals trend,
  new attending or other-specialty consult notes)

**Output shape:** Two-section brief — *"What was recommended"* (lifted
from the prior consult note), *"What happened with each recommendation"*
(followed / declined / modified, with cites). Plus *"What's new and
relevant to the consult question"*. The agent does not editorialize on
whether the team's compliance was appropriate.

**Why an agent (and specifically why not a dashboard):** This is
synthesis with a *personalized anchor*. The relevant filter is "what
*you* said last time" — the dashboard cannot know who is viewing or what
they recommended. A query on the chart cannot pull "the previous note
authored by this same consultant" without an LLM reading the note text.
Re-orientation against a personal anchor is genuinely agent-shaped.

**Why conversational:** Drill follow-ups: *"Did they actually start the
metoprolol?"*, *"Was the echo done? What did it show?"*, *"Is the rate
controlled now?"* The consultant's mental model is anchored on their
prior recommendations and updates incrementally; the conversation lets
them advance the model one item at a time.

---

## Secondary user: The Inpatient Clinical Pharmacist

A clinical pharmacist embedded on inpatient units, responsible for
reviewing medication orders for a defined panel of patients each day. Their
job is to verify dosing against organ function, catch interactions, ensure
prophylaxis where indicated, support antibiotic stewardship, and respond to
physician questions throughout the day. They are not prescribers; they
recommend changes and the prescriber decides.

Concrete profile:

- **Role:** Inpatient clinical pharmacist (PharmD)
- **Patient panel:** ~30–60 patients per day on assigned units
- **Time budget:** ~2–5 minutes per patient on a "screen" pass to flag
  who needs a deeper look; ~15–30 minutes for a deep med review on
  flagged patients
- **Familiarity:** Mid. They see the same units day after day so panel
  patients are not strangers, but the volume means they cannot keep
  every patient's nuance in their head.
- **Primary constraint:** Volume + depth. They cannot do a deep review
  on all 60 patients every day; they have to triage who needs one.

**Why agent leverage is high here.** Pharmacy work is the iconic
cross-source synthesis problem. A correct dose adjustment requires
combining the active med list + recent renal function trend + recent
hepatic function + active microbiology + allergies + active problems +
sometimes nutritional status. A correct stewardship recommendation
requires combining the antibiotic start date + culture results +
sensitivities + fever curve + WBC trend + the source-control note. No
dashboard composes these views. A hospitalist on rounds might do this
synthesis for one patient at a time, slowly. A pharmacist responsible
for 60 patients cannot.

### UC-10: Med-safety scan — "Which patients need a pharmacist deep review today?"

**The literal question:** *"Across my 40 patients on the medical units,
which ones have a medication that's now contraindicated, dose-mismatched
to current organ function, or part of a high-risk interaction pair?"*

**The 30 seconds before they open the agent.** Start of shift. The
pharmacist needs to triage their panel — most patients are stable on
appropriate regimens; a few have a flag worth investigating. Right now
that means opening each patient's med list, then their recent labs, then
checking interactions in a separate tool, then deciding whether to come
back. For 40 patients that is not happening every day.

**Sources the agent must read (per patient, fanned across the panel):**
- Active medication list, with start dates and recent dose changes
- Recent labs relevant to dosing: Cr, BUN, eGFR, AST/ALT/bili, INR, K,
  glucose
- Recent allergies (especially newly added)
- Active problem list (renal disease, hepatic disease, cardiac disease,
  diabetes — affects which adjustments matter)
- Microbiology results when antibiotics are active
- Known high-risk interaction pairs across the patient's full med list

**Output shape:** A ranked list of patients flagged for review, each
with the *specific concern* that triggered the flag — not a generic "may
need review." Example: *"Bed 14 (Lopez): vancomycin 1 g q12h ordered
2026-04-26; eGFR dropped from 58 to 41 since 04-27. Bed 21 (Patel):
warfarin + new bactrim ordered 04-28; INR not yet rechecked. Bed 30
(Chen): metformin still active; eGFR 28 — should be held."* Each flag
cites the rows that triggered it. The remainder of the panel is
explicitly listed as "no flags," not silently dropped.

**Why an agent (and specifically why not a dashboard):** This is the
canonical cross-source-correlation problem at scale. Each flag requires
combining the med list with multiple lab trends and a problem list. A
dashboard can show any one of these per patient; it cannot scan 40
patients and surface the seven who have a *combined* concern. An LLM
can. This is also where the audit-finding "tool reads structured rows
and reasons across them" becomes obviously load-bearing — the
pharmacist's whole workflow depends on it.

**Why conversational:** Drill into a flagged patient: *"Show me the
vancomycin trough trend"*, *"When was the eGFR last checked?"*,
*"What's the source for the vanco — is there a positive culture?"*,
*"Has the team been told?"* The pharmacist will then either contact the
prescriber directly or document a recommendation in the chart; the
conversation supports the work *up to* the recommendation, not the
recommendation itself (which the pharmacist owns).

---

### UC-11: Antibiotic stewardship — "Should this patient still be on broad-spectrum coverage?"

**The literal question:** *"Patient on cefepime + vancomycin since
2026-04-25 for empiric sepsis coverage. What was the source, did
cultures resolve, and can we narrow?"*

**The 30 seconds before they open the agent.** During the day,
stewardship review is interleaved with everything else. The pharmacist
knows broadly which patients are on broad-spectrum coverage; they need
to check whether each is still empiric, still appropriate, and whether
narrowing or stopping is supported by the chart. Right now, that means
opening per-patient: ABX history, micro lab, vitals, recent attending
note. Three to four tabs per patient, repeated across however many ABX
patients are on the unit.

**Sources the agent must read:**
- Antibiotic order history: drug, start date, dose, indication note,
  any prior changes
- Microbiology: cultures drawn, organisms identified, sensitivities,
  pending results
- Vitals: temperature curve since ABX start, hemodynamic stability
- Labs: WBC trend, lactate trend, CRP / procalcitonin if available, end
  organ markers (Cr, bili)
- Attending and ID consult notes that document source identification
  and stewardship plan

**Output shape:** A stewardship-focused summary — *"Empiric vs targeted
status"*, *"Time on therapy"*, *"What the cultures show"*, *"Narrowing
or stopping options the chart supports"*. Each item cites the source
record. The agent does not recommend a specific action; it surfaces what
the pharmacist would otherwise spend five minutes assembling so the
pharmacist can make the call.

**Why an agent (and specifically why not a dashboard):** This is the
flagship cross-source clinical question. A dashboard cannot connect
*"started cefepime on 04-25 for empiric sepsis"* to *"blood culture
grew E. coli sensitive to ceftriaxone on 04-27"* to *"no fever since
04-26"* to *"WBC normalized 04-27"* to surface *"the chart supports
de-escalation."* The reasoning across structured (ABX, labs, vitals) and
unstructured (attending note documenting the source) data is exactly
what an LLM does well that a fixed UI cannot.

**Why conversational:** *"What was the indication note?"*, *"Has ID
weighed in?"*, *"What does the most recent attending note say about the
plan?"*, *"Are there any other antibiotic options the patient hasn't
received?"* The conversation walks the pharmacist through the
narrowing/de-escalation decision; they then call the prescriber.

---

## What the secondary users do NOT change

Architecture, verification system, authorization model, observability, eval
strategy, demo data — all unchanged. These users plug in as additional
*launch contexts* (a different SMART scope set per role) and additional
*starter prompts* (so the agent knows it's serving a consultant or a
pharmacist when those roles open it). The agent's tools and verification
behavior are role-agnostic. Authorization scoping does change per role —
the pharmacist sees their unit's patients, the consultant sees patients
they have an active consult on, the hospitalist sees their care-team
patients — but the *mechanism* (a per-role membership query before any
chart read) is one design, three configurations.

---

## Non-Negotiables (Anti-Use-Cases)

These are explicit boundaries the agent must enforce. Each maps to a hard
constraint in the architecture and to test cases in the eval suite.

| Constraint | Behavior |
|---|---|
| **Read-only** | The agent never places orders, drafts notes that auto-save, or modifies the chart in any way. |
| **Source-grounded** | Every clinical claim cites the record it came from. Claims without a source are blocked, not softened. |
| **Authorization-scoped** | The agent only ever reads patients the requesting clinician is on service for. Cross-patient queries are filtered through that scope before retrieval, not after. |
| **No inference presented as fact** | "Patient took meds at 5 AM and may be sleeping now" is not allowed. The agent reports facts; the doctor draws inferences. |
| **No clinical recommendations** | The agent does not suggest doses, diagnoses, or treatment changes. It surfaces information; the physician decides. |
| **Graceful failure** | If a source is unavailable, the agent says so explicitly rather than answering from a partial picture. Silence on a missing source is a safety failure, not a UX inconvenience. |

---

## Out of scope for this project

The following are real hospitalist needs that the agent intentionally does not
address in week 1, to keep the scope honest:

- Discharge summary drafting
- Order entry or order suggestions
- Patient-facing communication
- Billing or coding assistance
- Cross-service handoff at end of shift

These are listed so reviewers can see the project recognizes them and chose not
to do them, rather than missed them.
