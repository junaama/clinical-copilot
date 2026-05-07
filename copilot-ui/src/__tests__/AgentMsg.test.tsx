import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import {
  AgentErrorBubble,
  AgentMsg,
  type AgentMessage,
} from '../components/AgentMsg';
import {
  MOCK_OVERNIGHT_BLOCK,
  MOCK_PLAIN_BLOCK,
  MOCK_TRIAGE_BLOCK,
} from '../fixtures/mockData';

function makeMsg(block: AgentMessage['block'], streaming = false): AgentMessage {
  return { role: 'agent', block, streaming };
}

describe('AgentMsg block dispatch', () => {
  it('renders the cohort block for kind=triage when not streaming', () => {
    render(
      <AgentMsg
        message={makeMsg(MOCK_TRIAGE_BLOCK)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    expect(screen.getByText('Wade235 Bednar518')).toBeInTheDocument();
    expect(screen.getByText('Maritza Calderón')).toBeInTheDocument();
    expect(screen.getByText('Jump to vitals →')).toBeInTheDocument();
  });

  it('renders deltas and timeline for kind=overnight', () => {
    render(
      <AgentMsg
        message={makeMsg(MOCK_OVERNIGHT_BLOCK)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    expect(screen.getByText('Tmax')).toBeInTheDocument();
    expect(screen.getByText('38.4°')).toBeInTheDocument();
    expect(screen.getByText('22:14')).toBeInTheDocument();
    expect(screen.getAllByText(/Nursing note/i).length).toBeGreaterThan(0);
  });

  it('renders only the lead for kind=plain', () => {
    render(
      <AgentMsg
        message={makeMsg(MOCK_PLAIN_BLOCK)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    expect(screen.queryByText('Wade235 Bednar518')).not.toBeInTheDocument();
    expect(screen.queryByText('Tmax')).not.toBeInTheDocument();
    expect(screen.getByText(/I can answer two things well/)).toBeInTheDocument();
  });

  it('hides body and citations while streaming', () => {
    render(
      <AgentMsg
        message={makeMsg(MOCK_TRIAGE_BLOCK, /* streaming */ true)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    expect(screen.queryByText('Wade235 Bednar518')).not.toBeInTheDocument();
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
  });

  it('calls onCite with the full citation when a chip is clicked', async () => {
    const user = userEvent.setup();
    const onCite = vi.fn();
    render(
      <AgentMsg
        message={makeMsg(MOCK_TRIAGE_BLOCK)}
        showCitations
        onCite={onCite}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    await user.click(screen.getByText('Vitals · last 4 readings'));
    expect(onCite).toHaveBeenCalledTimes(1);
    expect(onCite.mock.calls[0]?.[0]).toMatchObject({ card: 'vitals' });
  });

  it('omits citations when showCitations=false', () => {
    render(
      <AgentMsg
        message={makeMsg(MOCK_TRIAGE_BLOCK)}
        showCitations={false}
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
  });

  it('renders followup chips and forwards their label on click', async () => {
    const user = userEvent.setup();
    const onFollowup = vi.fn();
    render(
      <AgentMsg
        message={makeMsg(MOCK_TRIAGE_BLOCK)}
        showCitations
        onCite={vi.fn()}
        onFollowup={onFollowup}
        onJumpToVitals={vi.fn()}
      />,
    );
    await user.click(screen.getByText('Draft an SBAR for Wade235'));
    expect(onFollowup).toHaveBeenCalledWith('Draft an SBAR for Wade235');
  });

  it('triggers onJumpToVitals from the self row', async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    render(
      <AgentMsg
        message={makeMsg(MOCK_TRIAGE_BLOCK)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={onJump}
      />,
    );
    await user.click(screen.getByText('Jump to vitals →'));
    expect(onJump).toHaveBeenCalledTimes(1);
  });
});

describe('AgentMsg guideline citations (issue 027)', () => {
  const guidelinePlainBlock = {
    kind: 'plain' as const,
    lead: 'ADA suggests an A1c target below 7% for most non-pregnant adults.',
    citations: [
      {
        card: 'guideline' as const,
        label: 'ADA · 6.5',
        fhir_ref: 'guideline:ada-a1c-2024-1',
      },
    ],
    followups: [] as readonly string[],
  };

  it('renders a guideline source chip with the source · section label', () => {
    render(
      <AgentMsg
        message={makeMsg(guidelinePlainBlock)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    const chip = screen.getByText('ADA · 6.5');
    expect(chip).toBeInTheDocument();
    expect(chip.closest('button')?.dataset['card']).toBe('guideline');
  });

  it('passes the guideline citation back to onCite on click', async () => {
    const user = userEvent.setup();
    const onCite = vi.fn();
    render(
      <AgentMsg
        message={makeMsg(guidelinePlainBlock)}
        showCitations
        onCite={onCite}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    await user.click(screen.getByText('ADA · 6.5'));
    expect(onCite).toHaveBeenCalledTimes(1);
    expect(onCite.mock.calls[0]?.[0]).toMatchObject({
      card: 'guideline',
      fhir_ref: 'guideline:ada-a1c-2024-1',
    });
  });

  it('still hides the chip while the lead is streaming', () => {
    render(
      <AgentMsg
        message={makeMsg(guidelinePlainBlock, /* streaming */ true)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    expect(screen.queryByText('ADA · 6.5')).not.toBeInTheDocument();
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
  });
});

describe('AgentMsg chart medication citations (issue 040)', () => {
  const medicationPlainBlock = {
    kind: 'plain' as const,
    lead: 'Active home medications include metformin and lisinopril.',
    citations: [
      {
        card: 'medications' as const,
        label: 'metformin · 500 mg PO BID',
        fhir_ref: 'MedicationRequest/m1',
      },
      {
        card: 'medications' as const,
        label: 'lisinopril · [not specified on order]',
        fhir_ref: 'MedicationRequest/m2',
      },
    ],
    followups: [] as readonly string[],
  };

  it('renders chart medication chips with human-readable labels', () => {
    render(
      <AgentMsg
        message={makeMsg(medicationPlainBlock)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    const chip = screen.getByText('metformin · 500 mg PO BID');
    expect(chip).toBeInTheDocument();
    expect(chip.closest('button')?.dataset['card']).toBe('medications');
  });

  it('preserves the absence marker on a missing-field medication chip', () => {
    render(
      <AgentMsg
        message={makeMsg(medicationPlainBlock)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    // The frontend renders the absence marker verbatim — missing chart
    // fields read as missing in the source data, not as definitive
    // absence (issue 040 acceptance criterion 4).
    expect(
      screen.getByText('lisinopril · [not specified on order]'),
    ).toBeInTheDocument();
  });

  it('passes the medication citation back to onCite on click', async () => {
    const user = userEvent.setup();
    const onCite = vi.fn();
    render(
      <AgentMsg
        message={makeMsg(medicationPlainBlock)}
        showCitations
        onCite={onCite}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    await user.click(screen.getByText('metformin · 500 mg PO BID'));
    expect(onCite).toHaveBeenCalledTimes(1);
    expect(onCite.mock.calls[0]?.[0]).toMatchObject({
      card: 'medications',
      fhir_ref: 'MedicationRequest/m1',
    });
  });

  it('does not render the opaque resource-handle as a chip label', () => {
    render(
      <AgentMsg
        message={makeMsg(medicationPlainBlock)}
        showCitations
        onCite={vi.fn()}
        onFollowup={vi.fn()}
        onJumpToVitals={vi.fn()}
      />,
    );
    // The pre-issue-040 default, "MedicationRequest (medications)",
    // leaked the FHIR resource type into the chip. The new label is
    // human-readable; this assertion guards against regression.
    expect(
      screen.queryByText('MedicationRequest (medications)'),
    ).not.toBeInTheDocument();
  });
});

describe('AgentMsg chart medication chip click flow (issue 040)', () => {
  it('routes a medication chip through the chart-card path with planCitationClick', async () => {
    const { planCitationClick } = await import('../api/citations');
    const effect = planCitationClick({
      card: 'medications',
      label: 'metformin · 500 mg PO BID',
      fhir_ref: 'MedicationRequest/m1',
    });
    // The click effect must dispatch a chart-card flash so the existing
    // copilot:flash-card postMessage path runs (issue 040 acceptance
    // criterion 5).
    expect(effect).toEqual({
      kind: 'chart-card',
      card: 'medications',
      fhir_ref: 'MedicationRequest/m1',
    });
  });
});

describe('AgentMsg route badge (issue 039)', () => {
  it('renders the route label as a status badge when route is present', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: MOCK_OVERNIGHT_BLOCK,
      streaming: false,
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
    const badge = screen.getByRole('status');
    expect(badge).toHaveAttribute('data-route-kind', 'chart');
    expect(badge.textContent).toContain('Reading the patient record');
  });

  it('omits the route badge when route is absent (rehydrated turns)', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: MOCK_OVERNIGHT_BLOCK,
      streaming: false,
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
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('does not assume "Reading the patient record" for a panel route', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: MOCK_TRIAGE_BLOCK,
      streaming: false,
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
    const badge = screen.getByRole('status');
    expect(badge).toHaveAttribute('data-route-kind', 'panel');
    expect(badge.textContent).toContain('Reviewing your panel');
    expect(badge.textContent).not.toContain('Reading');
  });
});

describe('AgentMsg guideline failure UI (issue 041)', () => {
  // The backend produces a corpus-bound refusal whenever the guideline
  // retrieval call failed or the synthesizer leaked internal markers
  // (worker names, raw error tokens, HTTP statuses). The frontend
  // contract: render that refusal cleanly with a refusal route badge,
  // no source chips, and no leaked technical text.
  const refusalLead =
    "I couldn't reach the clinical guideline corpus this turn, " +
    "so I won't offer a recommendation. The answer would not be " +
    "grounded in retrieved guideline evidence. Please retry in a " +
    "moment, or consult the guideline directly.";

  const refusalBlock = {
    kind: 'plain' as const,
    lead: refusalLead,
    citations: [] as readonly Citation[],
    followups: [] as readonly string[],
  };

  type Citation = {
    card: 'guideline';
    label: string;
    fhir_ref: string;
  };

  it('renders the refusal route badge for a failed guideline turn', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: refusalBlock,
      streaming: false,
      route: { kind: 'refusal', label: 'Cannot ground this answer' },
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
    const badge = screen.getByRole('status');
    expect(badge).toHaveAttribute('data-route-kind', 'refusal');
    expect(badge.textContent).toContain('Cannot ground this answer');
  });

  it('renders the corpus-bound lead and no Sources section', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: refusalBlock,
      streaming: false,
      route: { kind: 'refusal', label: 'Cannot ground this answer' },
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
    expect(screen.getByText(/clinical guideline corpus/)).toBeInTheDocument();
    // No source chip section when there are zero citations.
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
  });

  it('does not surface internal technical markers in any rendered text', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: refusalBlock,
      streaming: false,
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
    const visible = container.textContent ?? '';
    // Clinical answer must read in product language, not debug language.
    expect(visible).not.toMatch(/no_active_user/);
    expect(visible).not.toMatch(/retrieval_failed/);
    expect(visible).not.toMatch(/evidence_retriever/);
    expect(visible).not.toMatch(/HTTP 4\d\d/);
    expect(visible).not.toMatch(/HTTP 5\d\d/);
  });
});

describe('AgentErrorBubble', () => {
  it('renders an HTTP status and detail', () => {
    render(<AgentErrorBubble status={502} detail="upstream FHIR timeout" />);
    expect(screen.getByText(/HTTP 502/)).toBeInTheDocument();
    expect(screen.getByText(/upstream FHIR timeout/)).toBeInTheDocument();
  });

  it('renders a "Network error" heading when status is 0', () => {
    render(<AgentErrorBubble status={0} detail="failed to fetch" />);
    expect(screen.getByText(/Network error/)).toBeInTheDocument();
  });
});

describe('AgentMsg panel triage failure UI (issue 042)', () => {
  // The backend produces a panel-unavailable refusal whenever the panel
  // tool returned ok:false or the synthesizer leaked internal markers
  // (probe names, raw error tokens, HTTP statuses). The frontend
  // contract: render the failure cleanly with the panel route badge
  // (so the route stays advertised), no source chips, and no leaked
  // technical text in the main answer.
  const refusalLead =
    "Panel data is unavailable right now, so I can't rank the patients " +
    'on your panel. Please retry in a moment, or pick a specific patient ' +
    'to ask about instead.';

  const refusalBlock = {
    kind: 'plain' as const,
    lead: refusalLead,
    citations: [] as never[],
    followups: [] as readonly string[],
  };

  it('renders the panel route badge with the unavailable label', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: refusalBlock,
      streaming: false,
      route: { kind: 'panel', label: 'Panel data unavailable' },
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
    const badge = screen.getByRole('status');
    // Route kind stays panel — issue 042 contract: failure is a state
    // of the panel route, not a different route.
    expect(badge).toHaveAttribute('data-route-kind', 'panel');
    expect(badge.textContent).toContain('Panel data unavailable');
    expect(badge.textContent).not.toContain('Cannot ground');
  });

  it('renders the product-safe lead and no Sources section', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: refusalBlock,
      streaming: false,
      route: { kind: 'panel', label: 'Panel data unavailable' },
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
    expect(screen.getByText(/Panel data is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
  });

  it('does not surface internal technical markers in the main answer', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: refusalBlock,
      streaming: false,
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
    const visible = container.textContent ?? '';
    // Clinical answer must read in product language, not debug language.
    expect(visible).not.toMatch(/run_panel_triage/);
    expect(visible).not.toMatch(/list_panel/);
    expect(visible).not.toMatch(/careteam_denied/);
    expect(visible).not.toMatch(/HTTP 4\d\d/);
    expect(visible).not.toMatch(/HTTP 5\d\d/);
    expect(visible).not.toMatch(/no_active_user/);
  });
});

describe('AgentMsg technical details affordance (issue 042)', () => {
  // The technical-details surface is hidden by default (via the browser's
  // <details>) so the clinical answer surface stays uncluttered. Tests
  // assert: the surface renders when debugInfo is provided, the closed
  // state hides the body, and the body carries the route + diagnostic
  // fields in identifiable shape so a developer can audit per-turn
  // routing.
  const baseRoute = {
    kind: 'panel' as const,
    label: 'Panel data unavailable',
  };
  const baseDiagnostics = {
    decision: 'tool_failure',
    supervisor_action: '',
  };
  const debugInfo = {
    route: baseRoute,
    workflow_id: 'W-1',
    classifier_confidence: 0.92,
    diagnostics: baseDiagnostics,
  };

  it('renders the Technical details disclosure when debugInfo is provided', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: MOCK_PLAIN_BLOCK,
      streaming: false,
      route: baseRoute,
      debugInfo,
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
    const surface = screen.getByTestId('agent-technical-details');
    expect(surface).toBeInTheDocument();
    // Closed by default — the body is in the DOM but the disclosure
    // triangle is closed (the browser hides everything except <summary>).
    expect((surface as HTMLDetailsElement).open).toBe(false);
    expect(screen.getByText('Technical details')).toBeInTheDocument();
  });

  it('omits the Technical details disclosure when debugInfo is absent', () => {
    // Rehydrated turns have no state envelope, so the AgentPanel does not
    // attach debugInfo. The technical-details surface must not appear in
    // that case.
    const msg: AgentMessage = {
      role: 'agent',
      block: MOCK_PLAIN_BLOCK,
      streaming: false,
      route: baseRoute,
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
    expect(
      screen.queryByTestId('agent-technical-details'),
    ).not.toBeInTheDocument();
  });

  it('exposes route, workflow, decision, and supervisor_action fields', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: MOCK_PLAIN_BLOCK,
      streaming: false,
      route: baseRoute,
      debugInfo: {
        route: baseRoute,
        workflow_id: 'W-1',
        classifier_confidence: 0.85,
        diagnostics: { decision: 'tool_failure', supervisor_action: 'extract' },
      },
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
    // Force-open so we can read the body content (the closed-state test
    // above pins the hidden-by-default behavior).
    const surface = screen.getByTestId(
      'agent-technical-details',
    ) as HTMLDetailsElement;
    surface.open = true;
    // Re-query body after opening so jsdom evaluates child layout.
    const fields = container.querySelectorAll('[data-field]');
    const byField = new Map<string, string>();
    fields.forEach((el) => {
      const k = el.getAttribute('data-field') ?? '';
      byField.set(k, el.textContent ?? '');
    });
    expect(byField.get('route-kind')).toBe('panel');
    expect(byField.get('route-label')).toBe('Panel data unavailable');
    expect(byField.get('workflow-id')).toBe('W-1');
    expect(byField.get('classifier-confidence')).toBe('0.85');
    expect(byField.get('decision')).toBe('tool_failure');
    expect(byField.get('supervisor-action')).toBe('extract');
  });

  it('hides the Technical details body while streaming', () => {
    const msg: AgentMessage = {
      role: 'agent',
      block: MOCK_PLAIN_BLOCK,
      streaming: true,
      route: baseRoute,
      debugInfo,
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
    // While the typewriter runs we suppress all body content (citations,
    // technical details, etc.) so they don't pop in mid-animation.
    expect(
      screen.queryByTestId('agent-technical-details'),
    ).not.toBeInTheDocument();
  });
});
