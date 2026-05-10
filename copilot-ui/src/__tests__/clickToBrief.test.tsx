/**
 * Patient-selection / prompt-pills integration test (issue 044, replaces
 * the issue-005 click-to-brief flow).
 *
 * Walks through the StandaloneApp shell from the empty-state CareTeam
 * panel through patient focus, the appearance of the patient-focused
 * prompt pills, and the pill-click chat round-trip.
 *
 * Mocks the boundary: GET /me, GET /panel, POST /chat. Everything else
 * is real React rendering — the AgentPanel <-> StandaloneApp wire is
 * exercised. The behavior under test is:
 *
 *  - Selecting a patient focuses that patient WITHOUT inserting an
 *    automatic chart brief into the transcript (issue 044 AC1).
 *  - Patient-focused prompt pills appear after selection (AC2/AC3).
 *  - Clicking a pill sends a user-visible prompt (AC4).
 *  - The transcript shows the pill's prompt as a normal user turn,
 *    distinguishable from prior auto-generated brief behavior (AC5).
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../App';
import { MOCK_OVERNIGHT_RESPONSE } from '../fixtures/mockData';

const ORIGINAL_FETCH = globalThis.fetch;

interface FetchStub {
  readonly chatBodies: string[];
  reset(): void;
}

function installFetchMock(): FetchStub {
  const chatBodies: string[] = [];
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
              given_name: 'Robert123',
              family_name: 'Hayes456',
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
  return {
    chatBodies,
    reset: () => {
      chatBodies.length = 0;
    },
  };
}

describe('patient selection focuses patient without auto-brief (issue 044)', () => {
  let stub: FetchStub;

  beforeEach(() => {
    stub = installFetchMock();
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    stub.reset();
  });

  it('clicking a panel patient does NOT inject a synthetic brief and does NOT fire /chat (AC1)', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    // No "Give me a brief on Robert Hayes." injected as a user message.
    // Allow a short window for any stray effect to fire.
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(
      screen.queryByText(/Give me a brief on Robert Hayes/i),
    ).not.toBeInTheDocument();
    // No /chat round-trip either.
    expect(stub.chatBodies.length).toBe(0);
  });

  it('focuses the patient — patient-focused welcome and pill labels render after selection (AC2)', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    // Patient-focused welcome headline.
    expect(
      await screen.findByText('How can I help with this chart?'),
    ).toBeInTheDocument();
    // The three patient pills render with the selected patient's name.
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

  it('clicking the brief pill sends the explicit user-visible prompt and fires /chat (AC4)', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    // Click the brief pill — this is the explicit user action.
    const briefPill = await screen.findByText('Get brief on Robert Hayes');
    await userEvent.click(briefPill);

    // The pill's prompt text appears as a user turn in the transcript.
    await screen.findByText(/Give me a brief on Robert Hayes\./);
    // No "auto-asked" tag — the pill click is a normal user turn, NOT an
    // auto-generated brief (AC5: distinguishable from prior auto behavior).
    expect(screen.queryByText(/auto-asked/i)).not.toBeInTheDocument();

    await waitFor(() => expect(stub.chatBodies.length).toBeGreaterThanOrEqual(1));
    const body = JSON.parse(stub.chatBodies[stub.chatBodies.length - 1] ?? '{}') as {
      message?: string;
      patient_id?: string;
    };
    expect(body.message).toBe('Give me a brief on Robert Hayes.');
    expect(body.patient_id).toBe('fixture-3');
  });

  it('clicking the medications pill sends the medications prompt (AC3/AC4)', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    const medsPill = await screen.findByText('Get medications on Robert Hayes');
    await userEvent.click(medsPill);

    await screen.findByText(/What medications is Robert Hayes on\?/);
    await waitFor(() => expect(stub.chatBodies.length).toBeGreaterThanOrEqual(1));
    const body = JSON.parse(stub.chatBodies[stub.chatBodies.length - 1] ?? '{}') as {
      message?: string;
    };
    expect(body.message).toBe('What medications is Robert Hayes on?');
  });

  it('clicking the overnight pill sends the overnight-trends prompt (AC3/AC4)', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    const overnightPill = await screen.findByText(
      'Overnight trends for Robert Hayes',
    );
    await userEvent.click(overnightPill);

    await screen.findByText(/What happened overnight for Robert Hayes\?/);
    await waitFor(() => expect(stub.chatBodies.length).toBeGreaterThanOrEqual(1));
    const body = JSON.parse(stub.chatBodies[stub.chatBodies.length - 1] ?? '{}') as {
      message?: string;
    };
    expect(body.message).toBe('What happened overnight for Robert Hayes?');
  });

  it('keeps the care-team sidebar visible after selection so the clinician can switch patients', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    expect(screen.getByText(/Your patients/i)).toBeInTheDocument();

    await userEvent.click(row);

    // Patient pills render in the welcome card; the care-team panel stays
    // mounted in the right sidebar.
    await screen.findByText('Get brief on Robert Hayes');
    expect(screen.getByText(/Your patients/i)).toBeInTheDocument();
  });

  it('fires /chat exactly once per pill click (no duplicate fires from re-renders)', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    const briefPill = await screen.findByText('Get brief on Robert Hayes');
    await userEvent.click(briefPill);

    await screen.findByText(/Give me a brief on Robert Hayes\./);
    await waitFor(() => expect(stub.chatBodies.length).toBe(1));

    // Stable across re-renders.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(stub.chatBodies.length).toBe(1);
  });
});
