/**
 * Vitest-only fixtures derived from the design prototype's data.jsx.
 *
 * DO NOT IMPORT FROM RUNTIME CODE. The agent backend is the only source of
 * truth for live answers. These shapes exist to test the dispatcher and the
 * type parser without standing up the backend.
 */

import type { ChatResponse, OvernightBlock, PlainBlock, TriageBlock } from '../api/types';

export const MOCK_TRIAGE_BLOCK: TriageBlock = {
  kind: 'triage',
  lead: '3 of your 5 patients have something new since 22:00. Wade235 is the highest-priority by a wide margin — possible wound infection with a rising NEWS2.',
  cohort: [
    {
      id: 'p1',
      name: 'Wade235 Bednar518',
      age: 33,
      room: 'MS-412',
      score: 86,
      trend: 'up',
      self: true,
      fhir_ref: 'Patient/4',
      reasons: [
        'NEWS2 +3 since 22:00 (HR↑, T↑, SpO₂↓)',
        'WBC 14.8, CRP 86, lactate 2.4 — possible wound infection',
        'Burn dressing not changed in 26 h',
      ],
    },
    {
      id: 'p2',
      name: 'Maritza Calderón',
      age: 71,
      room: 'MS-407',
      score: 72,
      trend: 'up',
      self: false,
      fhir_ref: 'Patient/5',
      reasons: [
        'New AF with RVR at 03:14, rate 138',
        'Held home metoprolol — pharmacy query open',
      ],
    },
    {
      id: 'p3',
      name: 'Joseph Lindqvist',
      age: 58,
      room: 'MS-419',
      score: 54,
      trend: 'flat',
      self: false,
      fhir_ref: 'Patient/6',
      reasons: ['Discharge ready — pending PT sign-off', 'No overnight events'],
    },
    {
      id: 'p4',
      name: 'Asha Devarakonda',
      age: 44,
      room: 'MS-403',
      score: 41,
      trend: 'down',
      self: false,
      fhir_ref: 'Patient/7',
      reasons: ['Pain trending down (6 → 2)', 'Tolerated PO overnight'],
    },
    {
      id: 'p5',
      name: 'Roy Bertilsson',
      age: 67,
      room: 'MS-415',
      score: 38,
      trend: 'flat',
      self: false,
      fhir_ref: 'Patient/8',
      reasons: ['Stable, awaiting AM labs'],
    },
  ],
  citations: [
    { card: 'vitals', label: 'Vitals · last 4 readings', fhir_ref: 'Observation/vital-789' },
    { card: 'labs', label: 'Labs · 04:30', fhir_ref: 'DiagnosticReport/lab-123' },
    { card: 'problems', label: 'Problem: partial thickness burn', fhir_ref: 'Condition/burn-1' },
  ],
  followups: ['Draft an SBAR for Wade235', 'Sort cohort by NEWS2 instead', 'Open Maritza Calderón'],
};

export const MOCK_OVERNIGHT_BLOCK: OvernightBlock = {
  kind: 'overnight',
  lead: 'Between 22:00 and 06:30, Wade235 spiked a fever, vitals trended toward sepsis criteria, and the team started a workup. No antibiotics yet.',
  deltas: [
    { label: 'Tmax', from: '37.2°', to: '38.4°', dir: 'up' },
    { label: 'HR', from: '88', to: '104', dir: 'up' },
    { label: 'SpO₂', from: '97%', to: '94%', dir: 'down' },
    { label: 'Pain (R forearm)', from: '4/10', to: '7/10', dir: 'up' },
  ],
  timeline: [
    {
      t: '22:14',
      kind: 'Nursing note',
      text: 'Pt reports increased pain at burn site (R forearm), 5/10. Dressing intact. Repositioned.',
      fhir_ref: null,
    },
    {
      t: '23:02',
      kind: 'Med admin',
      text: 'Acetaminophen/Hydrocodone 325/7.5 mg PO ×1 — pain 5/10 → 3/10 at 23:45.',
      fhir_ref: 'MedicationAdministration/ma-1',
    },
    {
      t: '02:40',
      kind: 'Order',
      text: 'Blood cultures ×2, CBC + CMP, lactate, CRP. Wound swab from R forearm.',
      fhir_ref: 'ServiceRequest/sr-1',
    },
    {
      t: '04:30',
      kind: 'Lab',
      text: 'WBC 14.8 H · CRP 86 H · Lactate 2.4 H · Cr 1.32 H.',
      fhir_ref: 'DiagnosticReport/lab-123',
    },
    {
      t: '06:12',
      kind: 'Nursing note',
      text: 'Tmax 38.4 °C, HR 104, BP 156/98, SpO₂ 94 %. Pain 7/10. MD aware.',
      fhir_ref: null,
    },
  ],
  citations: [
    { card: 'vitals', label: 'Vitals trend', fhir_ref: 'Observation/vital-789' },
    { card: 'labs', label: 'Labs · WBC, CRP, lactate', fhir_ref: 'DiagnosticReport/lab-123' },
    { card: 'medications', label: 'Active meds', fhir_ref: null },
  ],
  followups: ["Suggest next orders", "Show last night's vitals trend", 'Draft progress note'],
};

export const MOCK_PLAIN_BLOCK: PlainBlock = {
  kind: 'plain',
  lead: "I can answer two things well right now: who on your panel needs attention first, and what changed for this patient overnight.",
  citations: [],
  followups: ['Who needs attention first?', 'What happened overnight?'],
};

export const MOCK_TRIAGE_RESPONSE: ChatResponse = {
  conversation_id: 'demo-1',
  reply: MOCK_TRIAGE_BLOCK.lead,
  block: MOCK_TRIAGE_BLOCK,
  state: {
    patient_id: '4',
    workflow_id: 'W-1',
    classifier_confidence: 0.93,
    message_count: 2,
    route: { kind: 'panel', label: 'Reviewing your panel' },
  },
};

export const MOCK_OVERNIGHT_RESPONSE: ChatResponse = {
  conversation_id: 'demo-2',
  reply: MOCK_OVERNIGHT_BLOCK.lead,
  block: MOCK_OVERNIGHT_BLOCK,
  state: {
    patient_id: '4',
    workflow_id: 'W-2',
    classifier_confidence: 0.91,
    message_count: 4,
    route: { kind: 'chart', label: 'Reading the patient record' },
  },
};

export const MOCK_PLAIN_RESPONSE: ChatResponse = {
  conversation_id: 'demo-3',
  reply: MOCK_PLAIN_BLOCK.lead,
  block: MOCK_PLAIN_BLOCK,
  state: {
    patient_id: '4',
    workflow_id: 'unclear',
    classifier_confidence: 0.42,
    message_count: 1,
    route: { kind: 'clarify', label: 'Asking for clarification' },
  },
};
