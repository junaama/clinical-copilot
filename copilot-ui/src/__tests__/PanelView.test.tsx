import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PanelView } from '../components/PanelView';

const ORIGINAL_FETCH = globalThis.fetch;

function mockFetchOnce(body: unknown, ok = true): void {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok,
    json: async () => body,
  } as Response);
}

describe('PanelView', () => {
  beforeEach(() => {
    // Each test installs its own mock; reset to a known-failing default so
    // a missed mock surfaces as a test failure, not a hung promise.
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false } as Response);
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
  });

  it('renders the patient roster from /panel', async () => {
    mockFetchOnce({
      user_id: 42,
      patients: [
        {
          patient_id: 'fixture-1',
          given_name: 'Eduardo',
          family_name: 'Perez',
          birth_date: '1958-03-12',
          last_admission: '2026-04-30T10:00:00Z',
          room: null,
        },
        {
          patient_id: 'fixture-3',
          given_name: 'Robert',
          family_name: 'Hayes',
          birth_date: '1949-11-04',
          last_admission: null,
          room: null,
        },
      ],
    });

    render(<PanelView />);

    expect(await screen.findByText('Perez, Eduardo')).toBeInTheDocument();
    expect(screen.getByText('Hayes, Robert')).toBeInTheDocument();
    expect(screen.getByText(/DOB 1958-03-12/)).toBeInTheDocument();
    expect(screen.getByText(/Last admit 2026-04-30/)).toBeInTheDocument();
  });

  it('cleans synthetic numeric suffixes from roster display names', async () => {
    mockFetchOnce({
      user_id: 42,
      patients: [
        {
          patient_id: 'fixture-1',
          given_name: 'Patricia625 Raquel318',
          family_name: 'Covarrubias273',
          birth_date: '1958-03-12',
          last_admission: null,
          room: null,
        },
      ],
    });

    render(<PanelView />);

    expect(
      await screen.findByText('Covarrubias, Patricia Raquel'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Covarrubias273/)).not.toBeInTheDocument();
  });

  it('shows the empty-state copy when the panel is empty', async () => {
    mockFetchOnce({ user_id: 42, patients: [] });

    render(<PanelView />);

    expect(
      await screen.findByText(/No patients on your panel/i),
    ).toBeInTheDocument();
  });

  it('shows an error state when /panel fails', async () => {
    mockFetchOnce({}, /* ok */ false);

    render(<PanelView />);

    expect(
      await screen.findByText(/Couldn’t load your patient panel/i),
    ).toBeInTheDocument();
  });

  it('invokes onPatientClick when a row is clicked', async () => {
    mockFetchOnce({
      user_id: 42,
      patients: [
        {
          patient_id: 'fixture-1',
          given_name: 'Eduardo',
          family_name: 'Perez',
          birth_date: '1958-03-12',
          last_admission: null,
          room: null,
        },
      ],
    });
    const onClick = vi.fn();

    render(<PanelView onPatientClick={onClick} />);

    const row = await screen.findByRole('button', { name: /Perez, Eduardo/ });
    await userEvent.click(row);

    await waitFor(() => expect(onClick).toHaveBeenCalledTimes(1));
    expect(onClick.mock.calls[0]?.[0]).toMatchObject({
      patient_id: 'fixture-1',
      family_name: 'Perez',
    });
  });
});
