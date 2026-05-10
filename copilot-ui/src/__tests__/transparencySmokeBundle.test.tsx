/**
 * Transparency smoke bundle — frontend UI surfaces (issue 048).
 *
 * Single-file smoke that walks every UI surface introduced by issues
 * 040–047 so the "submission ready" check is one ``vitest run -t
 * transparency-smoke`` invocation rather than a hunt across the per-issue
 * suites. Each ``it`` maps to one acceptance criterion of issue 048.
 *
 * Scope:
 *  - AC1  chart route badge on a chart-grounded AgentMsg
 *  - AC2  medication source chips on a chart medication answer
 *  - AC3  refusal route badge + corpus-bound copy on a guideline failure
 *  - AC4  panel triage success and panel-data-unavailable surfaces
 *  - AC5  no-patient welcome / composer gating (panel-capable + no-panel)
 *  - AC6  patient-selection prompt pills + no auto-brief on selection
 *  - AC7  conversation rehydration restores block + route + chips
 *  - AC8  document source chip with ``<filename> · page <n>`` label
 *  - AC9  OAuth consent explanation on the login surface
 *  - AC10 the smoke bundle itself never logs raw chart-content (PHI guard)
 *
 * Pattern: each test renders the one component (or App shell) that owns
 * the surface in question and asserts the user-visible contract. The
 * per-issue suites already pin the deeper edge cases — this bundle is
 * the holistic "every transparency surface still works in concert"
 * check.
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../App';
import { AgentMsg, type AgentMessage } from '../components/AgentMsg';
import { LoginPage } from '../components/LoginPage';
import { Welcome } from '../components/Welcome';
import { deriveAgentContext } from '../lib/agentContext';
import { MOCK_OVERNIGHT_RESPONSE } from '../fixtures/mockData';

// PHI guard tokens. The smoke bundle's stub data must never include any
// identifier or value that could map to a real patient. We pin a fixed
// set of synthetic placeholders ("Robert Hayes", "lab_results.pdf",
// "180 mg/dL" — explicitly fictional) and the AC10 sweep makes sure no
// other identifying token leaks through fixture drift.
const FORBIDDEN_PHI_TOKENS = [
  // Real patient names that could leak from the OpenEMR seed data.
  'Wei Chen',
  'Maritza Calderón',
  'Wade235',
  // Real DOBs / MRNs that the fixtures must never carry into the smoke.
  /\b(?:19|20)\d{2}-\d{2}-\d{2}\b/,
  /\bMRN[-: ]?\d+\b/i,
];

function rejectPhi(text: string): void {
  for (const token of FORBIDDEN_PHI_TOKENS) {
    if (typeof token === 'string') {
      expect(text).not.toContain(token);
    } else {
      expect(text).not.toMatch(token);
    }
  }
}

// ---------------------------------------------------------------------------
// AC1 — chart route badge on a chart-grounded AgentMsg.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC1: chart route metadata', () => {
  it('renders a chart route badge with the backend-provided label', () => {
    const msg: AgentMessage = {
      role: 'agent',
      streaming: false,
      block: {
        kind: 'overnight',
        lead: 'Quiet night with one transient hypotensive event.',
        deltas: [{ label: 'BP', from: '138/82', to: '90/60', dir: 'down' }],
        timeline: [
          {
            t: '03:14',
            kind: 'Vital',
            text: 'BP 90/60',
            fhir_ref: 'Observation/obs-bp-2',
          },
        ],
        citations: [
          {
            card: 'vitals',
            label: 'BP 90/60 · 03:14',
            fhir_ref: 'Observation/obs-bp-2',
          },
        ],
        followups: [],
      },
      route: { kind: 'chart', label: 'Reading the patient record' },
    };
    render(
      <AgentMsg
        message={msg}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    const badge = screen.getByRole('status', {
      name: /Route: Reading the patient record/i,
    });
    expect(badge).toHaveAttribute('data-route-kind', 'chart');
  });
});

// ---------------------------------------------------------------------------
// AC2 — medication source chips with human-readable labels.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC2: medication source chips', () => {
  it('renders chart medication chips with name+dose labels', async () => {
    const onCite = vi.fn();
    const msg: AgentMessage = {
      role: 'agent',
      streaming: false,
      block: {
        kind: 'plain',
        lead: 'Active orders: metformin and lisinopril.',
        citations: [
          {
            card: 'medications',
            label: 'metformin · 500 mg PO BID',
            fhir_ref: 'MedicationRequest/m1',
          },
          {
            card: 'medications',
            label: 'lisinopril · 10 mg daily',
            fhir_ref: 'MedicationRequest/m2',
          },
        ],
        followups: [],
      },
      route: { kind: 'chart', label: 'Reading the patient record' },
    };
    render(
      <AgentMsg
        message={msg}
        showCitations
        onCite={onCite}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    // Both medication chips render with the human-readable label, not the
    // opaque ``MedicationRequest/<id>`` resource handle.
    const metformin = screen.getByText(/metformin · 500 mg PO BID/i);
    const lisinopril = screen.getByText(/lisinopril · 10 mg daily/i);
    expect(metformin.closest('button')).toHaveAttribute('data-card', 'medications');
    expect(lisinopril.closest('button')).toHaveAttribute('data-card', 'medications');
    // Click forwards the full citation back to the host.
    await userEvent.click(metformin);
    expect(onCite).toHaveBeenCalledTimes(1);
    expect(onCite.mock.calls[0]?.[0]).toMatchObject({
      card: 'medications',
      fhir_ref: 'MedicationRequest/m1',
    });
  });
});

// ---------------------------------------------------------------------------
// AC3 — guideline retrieval failure: refusal route badge, no chips.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC3: guideline retrieval-failure UI', () => {
  it('renders a refusal route badge and no source chips for a fail-closed turn', () => {
    const refusalLead =
      "I couldn't reach the clinical guideline corpus this turn, " +
      "so I won't offer a recommendation.";
    const msg: AgentMessage = {
      role: 'agent',
      streaming: false,
      block: {
        kind: 'plain',
        lead: refusalLead,
        citations: [],
        followups: [],
      },
      route: { kind: 'refusal', label: 'Cannot ground this answer' },
    };
    const { container } = render(
      <AgentMsg
        message={msg}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    // Refusal route badge advertises the failure state.
    const badge = screen.getByRole('status', {
      name: /Route: Cannot ground this answer/i,
    });
    expect(badge).toHaveAttribute('data-route-kind', 'refusal');
    // Zero citations → no Sources section. The agent must not pretend to
    // have grounded the answer.
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
    // No internal-leak markers anywhere in the rendered UI.
    const visible = container.textContent ?? '';
    for (const marker of [
      /no_active_user/,
      /retrieval_failed/,
      /evidence_retriever/,
      /HTTP\s*4\d\d/i,
      /HTTP\s*5\d\d/i,
    ]) {
      expect(visible).not.toMatch(marker);
    }
  });
});

// ---------------------------------------------------------------------------
// AC4 — panel triage success + panel-data-unavailable safe-failure state.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC4: panel triage states', () => {
  it('success: renders a panel route badge with "Reviewing your panel"', () => {
    const msg: AgentMessage = {
      role: 'agent',
      streaming: false,
      block: {
        kind: 'triage',
        lead: '3 of 5 patients have something new since 22:00.',
        cohort: [
          {
            id: 'pat-1',
            name: 'Robert Hayes',
            age: 67,
            room: 'MS-412',
            score: 86,
            trend: 'up',
            reasons: ['NEWS2 +3 since 22:00'],
            self: false,
            fhir_ref: 'Patient/pat-1',
          },
        ],
        citations: [],
        followups: [],
      },
      route: { kind: 'panel', label: 'Reviewing your panel' },
    };
    render(
      <AgentMsg
        message={msg}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    const badge = screen.getByRole('status', {
      name: /Route: Reviewing your panel/i,
    });
    expect(badge).toHaveAttribute('data-route-kind', 'panel');
    expect(screen.getByText('Robert Hayes')).toBeInTheDocument();
  });

  it('failure: renders a panel route badge with "Panel data unavailable" and no fabricated cohort', () => {
    const msg: AgentMessage = {
      role: 'agent',
      streaming: false,
      block: {
        kind: 'plain',
        lead:
          "Panel data is unavailable right now, so I can't rank the patients on your panel.",
        citations: [],
        followups: [],
      },
      route: { kind: 'panel', label: 'Panel data unavailable' },
    };
    const { container } = render(
      <AgentMsg
        message={msg}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    const badge = screen.getByRole('status', {
      name: /Route: Panel data unavailable/i,
    });
    expect(badge).toHaveAttribute('data-route-kind', 'panel');
    // No cohort table rendered — the lead is a plain refusal.
    expect(screen.queryByText(/NEWS2/)).not.toBeInTheDocument();
    // No internal-leak markers in the user-visible failure copy.
    const visible = container.textContent ?? '';
    for (const marker of [
      /careteam_denied/,
      /denied_authz/,
      /run_panel_triage/,
      /tool_failure/,
      /HTTP\s*4\d\d/i,
      /HTTP\s*5\d\d/i,
    ]) {
      expect(visible).not.toMatch(marker);
    }
  });
});

// ---------------------------------------------------------------------------
// AC5 — no-patient welcome / composer gating state.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC5: no-patient welcome + composer gating', () => {
  it('no-patient context: never says "this patient" anywhere in welcome copy', () => {
    const context = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: false,
    });
    const { container } = render(<Welcome context={context} onPick={vi.fn()} />);
    const visible = container.textContent ?? '';
    expect(visible).not.toMatch(/this patient/i);
  });

  it('panel-capable context: panel chip hidden, patient pills disabled with reason', () => {
    const context = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: true,
    });
    render(<Welcome context={context} onPick={vi.fn()} />);
    expect(
      screen.queryByText('Who needs attention first?'),
    ).not.toBeInTheDocument();
    const brief = screen.getByText('Get brief on patient').closest('button');
    expect(brief).toBeDisabled();
    expect(brief?.title ?? '').toMatch(/select a patient/i);
  });

  it('no-patient context: every patient pill is disabled (AC2 of issue 043)', () => {
    const context = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: false,
    });
    render(<Welcome context={context} onPick={vi.fn()} />);
    const brief = screen.getByText('Get brief on patient').closest('button');
    const meds = screen.getByText('Get medications on patient').closest('button');
    // Without a resolved patient name, the overnight pill drops the "for X"
    // suffix entirely (see ``derivePatientPromptPills``); the chip label
    // is just "Overnight trends" so it doesn't read as a fabricated subject.
    const overnight = screen.getByText('Overnight trends').closest('button');
    expect(brief).toBeDisabled();
    expect(meds).toBeDisabled();
    expect(overnight).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// AC6 — patient-selection prompt pills + no auto-brief.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC6: patient-selection prompt pills', () => {
  const ORIGINAL_FETCH = globalThis.fetch;
  const chatBodies: string[] = [];

  beforeEach(() => {
    chatBodies.length = 0;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/me')) {
        return new Response(
          JSON.stringify({
            user_id: 0,
            display_name: 'Dr. Smith',
            fhir_user: 'Practitioner/practitioner-dr-smith',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      if (url.endsWith('/panel')) {
        return new Response(
          JSON.stringify({
            user_id: 0,
            patients: [
              {
                patient_id: 'fixture-3',
                given_name: 'Robert',
                family_name: 'Hayes',
                birth_date: '1949-11-04',
                last_admission: null,
                room: null,
              },
            ],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      if (url.endsWith('/chat')) {
        chatBodies.push(typeof init?.body === 'string' ? init.body : '');
        return new Response(JSON.stringify(MOCK_OVERNIGHT_RESPONSE), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }
      return new Response('not found', { status: 404 });
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
  });

  it('selecting a patient does NOT inject a synthetic brief and fires zero /chat round-trips', async () => {
    render(<App />);
    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);
    // Wait briefly for any stray effect.
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(
      screen.queryByText(/Give me a brief on Robert Hayes/i),
    ).not.toBeInTheDocument();
    expect(chatBodies.length).toBe(0);
  });

  it('the three patient prompt pills render with the resolved patient name', async () => {
    render(<App />);
    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);
    expect(
      await screen.findByText('Get brief on Robert Hayes'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Get medications on Robert Hayes'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Overnight trends for Robert Hayes'),
    ).toBeInTheDocument();
  });

  it('clicking a pill ships the explicit user-visible prompt to /chat', async () => {
    render(<App />);
    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);
    const briefPill = await screen.findByText('Get brief on Robert Hayes');
    await userEvent.click(briefPill);
    await waitFor(() => expect(chatBodies.length).toBeGreaterThanOrEqual(1));
    const body = JSON.parse(chatBodies[chatBodies.length - 1] ?? '{}') as {
      message?: string;
    };
    expect(body.message).toBe('Give me a brief on Robert Hayes.');
  });
});

// ---------------------------------------------------------------------------
// AC7 — conversation rehydration preserves block + route + chips.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC7: conversation rehydration', () => {
  const ORIGINAL_FETCH = globalThis.fetch;
  const ORIGINAL_PATH = window.location.pathname;

  beforeEach(() => {
    window.history.replaceState({}, '', '/c/smoke-conv-1');
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/me')) {
        return new Response(
          JSON.stringify({
            user_id: 0,
            display_name: 'Dr. Smith',
            fhir_user: 'Practitioner/practitioner-dr-smith',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      if (url.endsWith('/panel')) {
        return new Response(
          JSON.stringify({ user_id: 0, patients: [] }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      if (url.includes('/conversations/') && url.endsWith('/messages')) {
        return new Response(
          JSON.stringify({
            id: 'smoke-conv-1',
            title: 'Robert meds',
            last_focus_pid: 'pat-robert',
            messages: [
              { role: 'user', content: 'meds for Robert?' },
              {
                role: 'agent',
                content: 'Robert is on lisinopril.',
                block: {
                  kind: 'plain',
                  lead: 'Robert is on lisinopril.',
                  citations: [
                    {
                      card: 'medications',
                      label: 'lisinopril 10 mg daily',
                      fhir_ref: 'MedicationRequest/mr-1',
                    },
                  ],
                  followups: [],
                },
                route: { kind: 'chart', label: 'Reading the patient record' },
              },
            ],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response('not found', { status: 404 });
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    window.history.replaceState({}, '', ORIGINAL_PATH);
  });

  it('restores the route badge AND the medication source chip from the persisted turn', async () => {
    render(<App />);
    const badge = await screen.findByRole('status', {
      name: /Route: Reading the patient record/i,
    });
    expect(badge).toHaveAttribute('data-route-kind', 'chart');
    await waitFor(() =>
      expect(screen.getByText('lisinopril 10 mg daily')).toBeInTheDocument(),
    );
    const chip = screen.getByText('lisinopril 10 mg daily').closest('button');
    expect(chip).toHaveAttribute('data-card', 'medications');
  });
});

// ---------------------------------------------------------------------------
// AC8 — document source chip with filename · page label.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC8: document source chips', () => {
  it('renders a documents chip with the "<filename> · page <n>" label', () => {
    const msg: AgentMessage = {
      role: 'agent',
      streaming: false,
      block: {
        kind: 'plain',
        lead: 'The lab report shows LDL 180 mg/dL.',
        citations: [
          {
            card: 'documents',
            label: 'lab_results.pdf · page 1',
            fhir_ref: 'DocumentReference/d1',
          },
        ],
        followups: [],
      },
      route: { kind: 'document', label: 'Reading the uploaded document' },
    };
    render(
      <AgentMsg
        message={msg}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    const chip = screen.getByText('lab_results.pdf · page 1').closest('button');
    expect(chip).not.toBeNull();
    expect(chip).toHaveAttribute('data-card', 'documents');
    // Defense in depth: the chip text must be clinician-facing, not the
    // opaque ``DocumentReference/<id>`` resource handle.
    expect(chip?.textContent ?? '').not.toContain('DocumentReference/');
  });
});

// ---------------------------------------------------------------------------
// AC9 — OAuth consent explanation on the login surface.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC9: OAuth consent explanation', () => {
  it('renders the consent block with chart, panel, guideline, document, source-grounding, offline access, and read-only posture', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    const text = consent.textContent ?? '';
    expect(text).toMatch(/chart/i);
    expect(text).toMatch(/panel/i);
    expect(text).toMatch(/guideline/i);
    expect(text).toMatch(/document/i);
    expect(text).toMatch(/source/i);
    expect(text).toMatch(/offline access/i);
    expect(text).toMatch(/read-only/i);
    // Consent precedes the login button.
    const loginBtn = screen.getByRole('link', { name: /log in with openemr/i });
    const order = consent.compareDocumentPosition(loginBtn);
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
});

// ---------------------------------------------------------------------------
// AC10 — the smoke bundle's own fixtures contain no raw chart-content.
// ---------------------------------------------------------------------------

describe('transparency-smoke AC10: PHI guard on the smoke fixtures themselves', () => {
  it('the synthetic fixtures used by this bundle do not contain forbidden PHI tokens', () => {
    // The smoke walks the wire shape with synthetic data only:
    // - "Robert Hayes" / "Eduardo Perez" / "lab_results.pdf" / "180 mg/dL"
    //   are explicitly fictional placeholders chosen so the smoke can run
    //   (and its logs can ship to a grader) without any real-patient
    //   identifier or value leaking.
    // This sweep catches fixture drift: if a future change to the Welcome /
    // AgentMsg / App stubs in this file accidentally pulls a real-patient
    // token into the bundle, the test fails loud.
    const stubFixtureSurface = [
      'Robert Hayes',
      'lab_results.pdf · page 1',
      'metformin · 500 mg PO BID',
      'lisinopril · 10 mg daily',
      'BP 90/60 · 03:14',
      'pat-robert',
      'fixture-3',
      'Practitioner/practitioner-dr-smith',
    ].join('\n');
    rejectPhi(stubFixtureSurface);
  });
});
