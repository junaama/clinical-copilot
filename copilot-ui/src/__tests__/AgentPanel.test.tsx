import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AgentPanel, type ChatMessage } from '../components/AgentPanel';

interface PanelHarnessProps {
  readonly focusPatientId: string;
  readonly hasPanelSurface: boolean;
  readonly patientName?: string;
}

function PanelHarness(props: PanelHarnessProps): JSX.Element {
  const messages: readonly ChatMessage[] = [];
  return (
    <AgentPanel
      open={true}
      surface="panel"
      density="regular"
      showCitations={true}
      accent="#4abfac"
      conversationId="conv-1"
      patientId={props.focusPatientId}
      userId="u-1"
      smartAccessToken=""
      patientName={props.patientName ?? ''}
      focusPatientId={props.focusPatientId}
      hasPanelSurface={props.hasPanelSurface}
      messages={messages}
      setMessages={() => {}}
      onCite={() => {}}
    />
  );
}

import type { JSX } from 'react';

describe('AgentPanel composer placeholder + Send button (issue 043)', () => {
  it('uses the no-patient placeholder when patient is unresolved and no panel surface (AC4)', () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={false} />);
    const input = screen.getByTestId('agent-input');
    expect(input).toHaveAttribute('placeholder');
    expect(input.getAttribute('placeholder') ?? '').toMatch(/select a patient/i);
    expect(input.getAttribute('placeholder') ?? '').not.toMatch(/this patient/i);
  });

  it('uses the panel-capable placeholder when no patient but panel surface mounted (AC4)', () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={true} />);
    const input = screen.getByTestId('agent-input');
    const placeholder = input.getAttribute('placeholder') ?? '';
    expect(placeholder).toMatch(/your panel/i);
    expect(placeholder).toMatch(/pick a patient/i);
    expect(placeholder).not.toMatch(/this patient/i);
  });

  it('uses the patient-focused placeholder once a patient is resolved (AC4)', () => {
    render(
      <PanelHarness
        focusPatientId="pat-1"
        hasPanelSurface={true}
        patientName="Robert Hayes"
      />,
    );
    const input = screen.getByTestId('agent-input');
    expect(input.getAttribute('placeholder') ?? '').toMatch(
      /this patient or your panel/i,
    );
  });

  it('Send button is disabled when the draft is empty (AC5)', () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={true} />);
    expect(screen.getByTestId('agent-send')).toBeDisabled();
  });

  it('Send button enables once the draft has content (AC5)', async () => {
    const user = userEvent.setup();
    render(<PanelHarness focusPatientId="pat-1" hasPanelSurface={true} />);
    const input = screen.getByTestId('agent-input');
    await user.type(input, 'hello');
    expect(screen.getByTestId('agent-send')).not.toBeDisabled();
  });

  it("Send button's title surfaces the visible disabled hint when empty (AC5)", () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={false} />);
    const send = screen.getByTestId('agent-send');
    expect(send.getAttribute('title') ?? '').toMatch(/select a patient/i);
  });

  it('renders a visible Send hint when no draft is present (AC5)', () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={true} />);
    const hint = screen.getByTestId('agent-send-hint');
    expect(hint).toBeInTheDocument();
    expect(hint.textContent ?? '').toMatch(/type|select|patient/i);
  });

  it('hides the Send hint once the draft has content', async () => {
    const user = userEvent.setup();
    render(<PanelHarness focusPatientId="" hasPanelSurface={true} />);
    expect(screen.queryByTestId('agent-send-hint')).toBeInTheDocument();
    await user.type(screen.getByTestId('agent-input'), 'who needs attention');
    expect(screen.queryByTestId('agent-send-hint')).not.toBeInTheDocument();
  });
});

describe('AgentPanel evidence slot', () => {
  it('renders extraction evidence inside the chat scroll region', () => {
    const { container } = render(
      <AgentPanel
        open={true}
        surface="panel"
        density="regular"
        showCitations={true}
        accent="#4abfac"
        conversationId="conv-1"
        patientId="pat-1"
        userId="u-1"
        smartAccessToken=""
        patientName="Robert Hayes"
        focusPatientId="pat-1"
        hasPanelSurface={true}
        messages={[]}
        setMessages={() => {}}
        onCite={() => {}}
        evidenceSlot={<section aria-label="uploaded extraction">Lab results</section>}
      />,
    );

    const scroll = container.querySelector('.agent-scroll');
    const evidence = screen.getByLabelText('uploaded extraction');
    expect(scroll).not.toBeNull();
    expect(scroll?.contains(evidence)).toBe(true);
  });
});

describe('AgentPanel header subtitle (issue 043 + 039)', () => {
  it('does not say "Reading this patient" when no patient is selected', () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={true} />);
    const subtitle = screen.getByTestId('agent-subtitle');
    expect(subtitle.textContent ?? '').not.toMatch(/this patient's record/i);
    // The standalone shell mounts a panel surface, so the no-patient
    // fallback subtitle is panel-aware.
    expect(subtitle.textContent ?? '').toMatch(/panel|FHIR/i);
  });

  it('uses the patient-record subtitle once a patient is resolved', () => {
    render(
      <PanelHarness
        focusPatientId="pat-1"
        hasPanelSurface={true}
        patientName="Robert Hayes"
      />,
    );
    const subtitle = screen.getByTestId('agent-subtitle');
    expect(subtitle.textContent ?? '').toMatch(/Robert Hayes's record/);
  });

  it('cleans synthetic numeric suffixes from the patient-record subtitle', () => {
    render(
      <PanelHarness
        focusPatientId="pat-1"
        hasPanelSurface={true}
        patientName="Adela471 Upton904"
      />,
    );
    const subtitle = screen.getByTestId('agent-subtitle');
    expect(subtitle.textContent ?? '').toMatch(/Adela Upton's record/);
    expect(subtitle.textContent ?? '').not.toMatch(/471|904/);
  });
});

describe('AgentPanel Welcome wiring (issue 043)', () => {
  it('renders the no-patient welcome copy in the chat scroll when no messages and no panel', () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={false} />);
    // Welcome card shows the headline from agentContext (no-patient kind).
    expect(screen.getByText('Select a patient to begin')).toBeInTheDocument();
  });

  it('renders the panel-capable welcome copy when no patient but panel surface', () => {
    render(<PanelHarness focusPatientId="" hasPanelSurface={true} />);
    expect(screen.getByText('How can I help today?')).toBeInTheDocument();
  });

  it('renders the patient-focused welcome copy when patient resolved', () => {
    render(
      <PanelHarness
        focusPatientId="pat-1"
        hasPanelSurface={true}
        patientName="Robert Hayes"
      />,
    );
    expect(
      screen.getByText('How can I help with this chart?'),
    ).toBeInTheDocument();
  });
});
