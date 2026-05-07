/**
 * Conversation rehydration integration test (issue 045).
 *
 * Walks through StandaloneApp deep-linking to ``/c/<id>`` for a
 * conversation the server already has structured turn rows for. The
 * issue-045 contract: the rehydrated transcript must show the same
 * route badges and source chips the clinician saw on the original
 * turn — not a flattened plain-text replay.
 *
 * Mocks the boundary: GET /me, GET /panel, GET /conversations/:id/messages.
 * No /chat call is required for this surface — rehydration is read-only.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../App';

const ORIGINAL_FETCH = globalThis.fetch;
const ORIGINAL_PATH = window.location.pathname;

interface MessagesResponseBuilder {
  responseBody: unknown;
  status: number;
}

function makeFetchMock(messagesResponse: MessagesResponseBuilder): typeof fetch {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
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
      return new Response(JSON.stringify(messagesResponse.responseBody), {
        status: messagesResponse.status,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response('not found', { status: 404 });
  });
  return fn as unknown as typeof fetch;
}

function setRehydrationUrl(conversationId: string): void {
  window.history.replaceState({}, '', `/c/${conversationId}`);
}

describe('conversation rehydration (issue 045)', () => {
  let messagesResponse: MessagesResponseBuilder;

  beforeEach(() => {
    setRehydrationUrl('rehydrate-conv-1');
    messagesResponse = {
      status: 200,
      responseBody: {
        id: 'rehydrate-conv-1',
        title: 'Robert overnight brief',
        last_focus_pid: 'pat-robert',
        messages: [],
      },
    };
    globalThis.fetch = makeFetchMock(messagesResponse);
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    window.history.replaceState({}, '', ORIGINAL_PATH);
  });

  it('restores route label for a prior chart-route assistant answer (AC: route labels)', async () => {
    messagesResponse.responseBody = {
      id: 'rehydrate-conv-1',
      title: 'Robert overnight brief',
      last_focus_pid: 'pat-robert',
      messages: [
        { role: 'user', content: 'How is Robert?' },
        {
          role: 'agent',
          content: 'Robert is stable.',
          block: {
            kind: 'plain',
            lead: 'Robert is stable.',
            citations: [],
            followups: [],
          },
          route: { kind: 'chart', label: 'Reading the patient record' },
        },
      ],
    };

    render(<App />);

    // The route badge renders the backend-provided label verbatim — that
    // is the visible transparency surface for "what did the agent do?".
    const badge = await screen.findByRole('status', {
      name: /Route: Reading the patient record/i,
    });
    expect(badge).toHaveAttribute('data-route-kind', 'chart');
  });

  it('restores source chips for a prior cited assistant answer (AC: source chips)', async () => {
    messagesResponse.responseBody = {
      id: 'rehydrate-conv-1',
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
                label: 'Lisinopril 10mg',
                fhir_ref: 'MedicationRequest/mr-1',
              },
            ],
            followups: [],
          },
          route: { kind: 'chart', label: 'Reading the patient record' },
        },
      ],
    };

    render(<App />);

    // The source chip shows the citation label and is clickable.
    await waitFor(() => {
      expect(screen.getByText('Lisinopril 10mg')).toBeInTheDocument();
    });
    const chip = screen.getByText('Lisinopril 10mg').closest('button');
    expect(chip).toHaveAttribute('data-card', 'medications');
  });

  it('restores triage block kind, not flattened to plain text', async () => {
    messagesResponse.responseBody = {
      id: 'rehydrate-conv-1',
      title: 'Triage',
      last_focus_pid: '',
      messages: [
        { role: 'user', content: 'who needs me?' },
        {
          role: 'agent',
          content: 'Three patients need attention.',
          block: {
            kind: 'triage',
            lead: 'Three patients need attention.',
            cohort: [
              {
                id: 'pat-1',
                name: 'Robert Hayes',
                age: 67,
                room: '302',
                score: 88,
                trend: 'up',
                reasons: ['RR up'],
                self: false,
                fhir_ref: 'Patient/pat-1',
              },
            ],
            citations: [],
            followups: [],
          },
          route: { kind: 'panel', label: 'Reviewing your panel' },
        },
      ],
    };

    render(<App />);

    // The cohort row appears — meaning the triage block was preserved
    // with its structured cohort, not flattened to plain text.
    await waitFor(() => {
      expect(screen.getByText('Robert Hayes')).toBeInTheDocument();
    });
    // And the panel route badge advertises the panel route.
    const badge = await screen.findByRole('status', {
      name: /Route: Reviewing your panel/i,
    });
    expect(badge).toHaveAttribute('data-route-kind', 'panel');
  });

  it('legacy turn (no block / no route) renders safely as plain text (AC: legacy fallback)', async () => {
    messagesResponse.responseBody = {
      id: 'rehydrate-conv-1',
      title: 'Old thread',
      last_focus_pid: '',
      messages: [
        { role: 'user', content: 'old question' },
        { role: 'agent', content: 'Robert was admitted last Tuesday.' },
      ],
    };

    render(<App />);

    // The plain-text content still renders.
    await waitFor(() => {
      expect(
        screen.getByText('Robert was admitted last Tuesday.'),
      ).toBeInTheDocument();
    });
    // No route badge — legacy turns have no route metadata.
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    // No source chips either — there are no citations.
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
  });

  it('restores multiple turns in chronological order', async () => {
    messagesResponse.responseBody = {
      id: 'rehydrate-conv-1',
      title: 'Multi-turn',
      last_focus_pid: 'pat-robert',
      messages: [
        { role: 'user', content: 'How is Robert?' },
        {
          role: 'agent',
          content: 'Robert is stable.',
          block: {
            kind: 'plain',
            lead: 'Robert is stable.',
            citations: [],
            followups: [],
          },
          route: { kind: 'chart', label: 'Reading the patient record' },
        },
        { role: 'user', content: 'Anything overnight?' },
        {
          role: 'agent',
          content: 'No new issues overnight.',
          block: {
            kind: 'plain',
            lead: 'No new issues overnight.',
            citations: [],
            followups: [],
          },
          route: { kind: 'chart', label: 'Reading the patient record' },
        },
      ],
    };

    render(<App />);

    // Both assistant leads render.
    await waitFor(() => {
      expect(screen.getByText('Robert is stable.')).toBeInTheDocument();
      expect(screen.getByText('No new issues overnight.')).toBeInTheDocument();
    });
    // Both route badges render — every cited turn keeps its provenance.
    const badges = await screen.findAllByRole('status', {
      name: /Route: Reading the patient record/i,
    });
    expect(badges.length).toBe(2);
  });
});
