/**
 * Click-to-brief integration test (issue 005).
 *
 * Walks through the StandaloneApp shell from the empty-state CareTeam panel
 * through the synthetic-message injection, the chat round-trip, and the
 * disappearance of the panel once the conversation has its first turn.
 *
 * Mocks the boundary: GET /me, GET /panel, POST /chat. Everything else is
 * real React rendering — the AgentPanel <-> StandaloneApp wire is exercised.
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
  return {
    chatBodies,
    reset: () => {
      chatBodies.length = 0;
    },
  };
}

describe('click-to-brief', () => {
  let stub: FetchStub;

  beforeEach(() => {
    stub = installFetchMock();
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    stub.reset();
  });

  it('clicking a panel patient injects the synthetic brief message and fires /chat', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    // The synthetic user message must be visibly rendered (AC: not hidden,
    // not styled differently, not collapsed).
    const userBubble = await screen.findByText(
      /Give me a brief on Robert Hayes\./,
    );
    expect(userBubble).toBeInTheDocument();
    // No "auto-asked" tag — click-to-brief is a normal user turn.
    expect(screen.queryByText(/auto-asked/i)).not.toBeInTheDocument();

    await waitFor(() => expect(stub.chatBodies.length).toBeGreaterThanOrEqual(1));
    const body = JSON.parse(stub.chatBodies[stub.chatBodies.length - 1] ?? '{}') as {
      message?: string;
    };
    expect(body.message).toBe('Give me a brief on Robert Hayes.');
  });

  it('keeps the care-team sidebar visible after a click triggers the first turn', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    expect(screen.getByText(/Your patients/i)).toBeInTheDocument();

    await userEvent.click(row);

    // Care-team panel lives in the right sidebar — it stays mounted so the
    // clinician can switch patients mid-conversation.
    await screen.findByText(/Give me a brief on Robert Hayes\./);
    expect(screen.getByText(/Your patients/i)).toBeInTheDocument();
  });

  it('fires /chat exactly once per click (id-dedupe in AgentPanel)', async () => {
    render(<App />);

    const row = await screen.findByRole('button', { name: /Hayes, Robert/i });
    await userEvent.click(row);

    // Wait for the first chat call to complete + the agent message to land.
    await screen.findByText(/Give me a brief on Robert Hayes\./);
    await waitFor(() => expect(stub.chatBodies.length).toBe(1));

    // Stable across re-renders — no duplicate fires from React effect re-runs.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(stub.chatBodies.length).toBe(1);
  });

});
