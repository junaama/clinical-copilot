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
