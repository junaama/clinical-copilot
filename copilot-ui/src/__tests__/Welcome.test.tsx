import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Welcome } from '../components/Welcome';
import { deriveAgentContext } from '../lib/agentContext';

describe('Welcome (issue 043, no-patient gating)', () => {
  describe('no-patient context (no patient, no panel surface)', () => {
    const context = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: false,
    });

    it('does not say or imply "this patient" anywhere in welcome copy (AC1)', () => {
      const { container } = render(
        <Welcome context={context} onPick={vi.fn()} />,
      );
      const visible = container.textContent ?? '';
      expect(visible).not.toMatch(/this patient/i);
      expect(visible).not.toMatch(/this patient's/i);
    });

    it('renders the patient pills as disabled with a clear reason (AC2)', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const brief = screen.getByText('Get brief on patient').closest('button');
      expect(brief).not.toBeNull();
      expect(brief).toBeDisabled();
      expect(brief).toHaveAttribute('aria-disabled', 'true');
      expect(brief?.title).toMatch(/select a patient/i);
    });

    it('does not render the panel suggestion while the shortcut is disabled', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      expect(
        screen.queryByText('Who needs attention first?'),
      ).not.toBeInTheDocument();
    });

    it('disabled patient pill does not invoke onPick when clicked', async () => {
      const onPick = vi.fn();
      const user = userEvent.setup();
      render(<Welcome context={context} onPick={onPick} />);
      const brief = screen.getByText('Get brief on patient');
      await user.click(brief);
      expect(onPick).not.toHaveBeenCalled();
    });
  });

  describe('panel-capable context (no patient, panel surface mounted)', () => {
    const context = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: true,
    });

    it('does not render the panel suggestion while the shortcut is disabled', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      expect(
        screen.queryByText('Who needs attention first?'),
      ).not.toBeInTheDocument();
    });

    it('keeps the patient pills disabled with a reason (AC2)', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const brief = screen.getByText('Get brief on patient').closest('button');
      expect(brief).toBeDisabled();
      expect(brief?.title).toMatch(/select a patient/i);
    });

    it('does not forward the panel suggestion while the shortcut is disabled', () => {
      const onPick = vi.fn();
      render(<Welcome context={context} onPick={onPick} />);
      expect(
        screen.queryByText('Who needs attention first?'),
      ).not.toBeInTheDocument();
      expect(onPick).not.toHaveBeenCalled();
    });

    it('does not say "this patient" in the welcome subcopy (AC1)', () => {
      const { container } = render(
        <Welcome context={context} onPick={vi.fn()} />,
      );
      const visible = container.textContent ?? '';
      expect(visible).not.toMatch(/this patient/i);
    });
  });

  describe('patient-focused context (patient resolved)', () => {
    const context = deriveAgentContext({
      focusPatientId: 'pat-1',
      focusPatientName: 'Robert Hayes',
      hasPanelSurface: true,
    });

    it('enables patient suggestions while the panel shortcut is disabled', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const brief = screen
        .getByText('Get brief on Robert Hayes')
        .closest('button');
      expect(brief).not.toBeDisabled();
      expect(
        screen.queryByText('Who needs attention first?'),
      ).not.toBeInTheDocument();
    });

    it("forwards the brief pill's promptText on click (issue 044)", async () => {
      const onPick = vi.fn();
      const user = userEvent.setup();
      render(<Welcome context={context} onPick={onPick} />);
      await user.click(screen.getByText('Get brief on Robert Hayes'));
      expect(onPick).toHaveBeenCalledWith('Give me a brief on Robert Hayes.');
    });

    it("renders the resolved patient name in the welcome subcopy", () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      expect(screen.getByText(/Robert Hayes's record/)).toBeInTheDocument();
    });
  });
});

describe('Welcome patient prompt pills (issue 044)', () => {
  const context = deriveAgentContext({
    focusPatientId: 'pat-1',
    focusPatientName: 'Robert Hayes',
    hasPanelSurface: true,
  });

  it('renders all three patient pills (brief, medications, overnight)', () => {
    render(<Welcome context={context} onPick={vi.fn()} />);
    expect(
      screen.getByText('Get brief on Robert Hayes'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Get medications on Robert Hayes'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Overnight trends for Robert Hayes'),
    ).toBeInTheDocument();
  });

  it("forwards the medications pill's promptText on click", async () => {
    const onPick = vi.fn();
    const user = userEvent.setup();
    render(<Welcome context={context} onPick={onPick} />);
    await user.click(screen.getByText('Get medications on Robert Hayes'));
    expect(onPick).toHaveBeenCalledWith(
      'What medications is Robert Hayes on?',
    );
  });

  it("forwards the overnight-trends pill's promptText on click", async () => {
    const onPick = vi.fn();
    const user = userEvent.setup();
    render(<Welcome context={context} onPick={onPick} />);
    await user.click(screen.getByText('Overnight trends for Robert Hayes'));
    expect(onPick).toHaveBeenCalledWith(
      'What happened overnight for Robert Hayes?',
    );
  });

  it("scopes pill labels to the focused patient (story 22)", () => {
    render(<Welcome context={context} onPick={vi.fn()} />);
    // Each pill mentions the patient by name.
    const buttons = screen
      .getAllByRole('button')
      .filter((b) => b.getAttribute('data-suggestion-kind') === 'patient');
    expect(buttons).toHaveLength(3);
    buttons.forEach((b) => {
      expect(b.textContent ?? '').toMatch(/Robert Hayes/);
    });
  });

  it("falls back to nameless pills when the patient name is a synthetic Patient/<id>", () => {
    // EHR-launch path uses ``Patient/<id>`` as the display label until a
    // server-side name lookup happens. Pills should not embed that
    // identifier in clinician-facing prompt text.
    const ehrContext = deriveAgentContext({
      focusPatientId: 'pat-9',
      focusPatientName: 'Patient/pat-9',
      hasPanelSurface: false,
    });
    render(<Welcome context={ehrContext} onPick={vi.fn()} />);
    expect(screen.getByText('Get brief on patient')).toBeInTheDocument();
    expect(
      screen.queryByText(/Get brief on Patient\/pat-9/),
    ).not.toBeInTheDocument();
  });
});
