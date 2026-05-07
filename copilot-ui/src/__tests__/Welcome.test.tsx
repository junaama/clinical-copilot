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

    it('renders the patient suggestion as disabled with a clear reason (AC2)', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const overnight = screen
        .getByText('What happened overnight?')
        .closest('button');
      expect(overnight).not.toBeNull();
      expect(overnight).toBeDisabled();
      expect(overnight).toHaveAttribute('aria-disabled', 'true');
      expect(overnight?.title).toMatch(/select a patient/i);
    });

    it('renders the panel suggestion as disabled when no panel surface (AC3)', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const attention = screen
        .getByText('Who needs attention first?')
        .closest('button');
      expect(attention).not.toBeNull();
      expect(attention).toBeDisabled();
    });

    it('disabled patient suggestion does not invoke onPick when clicked', async () => {
      const onPick = vi.fn();
      const user = userEvent.setup();
      render(<Welcome context={context} onPick={onPick} />);
      const overnight = screen.getByText('What happened overnight?');
      await user.click(overnight);
      expect(onPick).not.toHaveBeenCalled();
    });
  });

  describe('panel-capable context (no patient, panel surface mounted)', () => {
    const context = deriveAgentContext({
      focusPatientId: '',
      hasPanelSurface: true,
    });

    it('renders the panel suggestion as enabled (AC3)', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const attention = screen
        .getByText('Who needs attention first?')
        .closest('button');
      expect(attention).not.toBeNull();
      expect(attention).not.toBeDisabled();
      expect(attention).toHaveAttribute('aria-disabled', 'false');
    });

    it('keeps the patient suggestion disabled with a reason (AC2)', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const overnight = screen
        .getByText('What happened overnight?')
        .closest('button');
      expect(overnight).toBeDisabled();
      expect(overnight?.title).toMatch(/select a patient/i);
    });

    it('forwards the panel suggestion label on click', async () => {
      const onPick = vi.fn();
      const user = userEvent.setup();
      render(<Welcome context={context} onPick={onPick} />);
      await user.click(screen.getByText('Who needs attention first?'));
      expect(onPick).toHaveBeenCalledWith('Who needs attention first?');
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

    it('enables both the patient and panel suggestions', () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      const overnight = screen
        .getByText('What happened overnight?')
        .closest('button');
      const attention = screen
        .getByText('Who needs attention first?')
        .closest('button');
      expect(overnight).not.toBeDisabled();
      expect(attention).not.toBeDisabled();
    });

    it('forwards the patient suggestion label on click', async () => {
      const onPick = vi.fn();
      const user = userEvent.setup();
      render(<Welcome context={context} onPick={onPick} />);
      await user.click(screen.getByText('What happened overnight?'));
      expect(onPick).toHaveBeenCalledWith('What happened overnight?');
    });

    it("renders the resolved patient name in the welcome subcopy", () => {
      render(<Welcome context={context} onPick={vi.fn()} />);
      expect(screen.getByText(/Robert Hayes's record/)).toBeInTheDocument();
    });
  });
});
