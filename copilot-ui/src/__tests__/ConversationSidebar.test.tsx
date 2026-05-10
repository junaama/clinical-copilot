import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConversationSidebar } from '../components/ConversationSidebar';

const ORIGINAL_FETCH = globalThis.fetch;

interface MockResponse {
  readonly ok: boolean;
  readonly body: unknown;
}

function installFetchMock(
  responses: readonly MockResponse[],
  panelBody: unknown = { user_id: 42, patients: [] },
): ReturnType<typeof vi.fn> {
  const mock = vi.fn();
  let i = 0;
  mock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.endsWith('/panel')) {
      return {
        ok: true,
        json: async () => panelBody,
      } as Response;
    }
    const r = responses[Math.min(i, responses.length - 1)];
    i += 1;
    return {
      ok: r.ok,
      json: async () => r.body,
    } as Response;
  });
  globalThis.fetch = mock as unknown as typeof globalThis.fetch;
  return mock;
}

function countConversationFetches(mock: ReturnType<typeof vi.fn>): number {
  return mock.mock.calls.filter((call) => {
    const input = call[0] as RequestInfo | URL;
    const url = input instanceof Request ? input.url : String(input);
    return url.endsWith('/conversations');
  }).length;
}

function countPanelFetches(mock: ReturnType<typeof vi.fn>): number {
  return mock.mock.calls.filter((call) => {
    const input = call[0] as RequestInfo | URL;
    const url = input instanceof Request ? input.url : String(input);
    return url.endsWith('/panel');
  }).length;
}

describe('ConversationSidebar', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({}),
    } as Response);
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
  });

  it('lists conversations from GET /conversations', async () => {
    installFetchMock([
      {
        ok: true,
        body: {
          conversations: [
            {
              id: 'conv-newest',
              title: 'Brief on Eduardo',
              last_focus_pid: 'fixture-1',
              updated_at: 1000,
              created_at: 1000,
            },
            {
              id: 'conv-older',
              title: 'Panel triage',
              last_focus_pid: '',
              updated_at: 500,
              created_at: 500,
            },
          ],
        },
      },
    ]);

    render(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={0}
        onSelect={() => {}}
        onCreate={() => {}}
      />,
    );

    expect(await screen.findByText('Brief on Eduardo')).toBeInTheDocument();
    expect(screen.getByText('Panel triage')).toBeInTheDocument();
    expect(screen.getByText(/Patient fixture-1/)).toBeInTheDocument();
  });

  it('marks the active conversation row', async () => {
    installFetchMock([
      {
        ok: true,
        body: {
          conversations: [
            {
              id: 'conv-active',
              title: 'Active thread',
              last_focus_pid: '',
              updated_at: 1,
              created_at: 1,
            },
            {
              id: 'conv-inactive',
              title: 'Other thread',
              last_focus_pid: '',
              updated_at: 0,
              created_at: 0,
            },
          ],
        },
      },
    ]);

    render(
      <ConversationSidebar
        activeConversationId="conv-active"
        refreshToken={0}
        onSelect={() => {}}
        onCreate={() => {}}
      />,
    );

    const active = await screen.findByRole('button', { name: /Active thread/ });
    expect(active.getAttribute('aria-current')).toBe('true');
    const inactive = screen.getByRole('button', { name: /Other thread/ });
    expect(inactive.getAttribute('aria-current')).toBeNull();
  });

  it('invokes onSelect when a row is clicked', async () => {
    installFetchMock([
      {
        ok: true,
        body: {
          conversations: [
            {
              id: 'conv-clickme',
              title: 'Click me',
              last_focus_pid: '',
              updated_at: 0,
              created_at: 0,
            },
          ],
        },
      },
    ]);

    const onSelect = vi.fn();
    render(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={0}
        onSelect={onSelect}
        onCreate={() => {}}
      />,
    );

    const row = await screen.findByRole('button', { name: /Click me/ });
    await userEvent.click(row);

    expect(onSelect).toHaveBeenCalledWith('conv-clickme');
  });

  it('mints a new conversation and fires onCreate', async () => {
    installFetchMock([
      // Initial GET /conversations
      { ok: true, body: { conversations: [] } },
      // POST /conversations
      { ok: true, body: { id: 'new-thread-xyz' } },
    ]);

    const onCreate = vi.fn();
    render(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={0}
        onSelect={() => {}}
        onCreate={onCreate}
      />,
    );

    // Wait for the empty state so we know the first fetch resolved.
    await screen.findByText(/No conversations yet/i);

    const newBtn = screen.getByRole('button', { name: /New conversation/i });
    await userEvent.click(newBtn);

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('new-thread-xyz'));
  });

  it('falls back to (untitled) when title is blank', async () => {
    installFetchMock([
      {
        ok: true,
        body: {
          conversations: [
            {
              id: 'conv-blank',
              title: '',
              last_focus_pid: '',
              updated_at: 0,
              created_at: 0,
            },
          ],
        },
      },
    ]);

    render(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={0}
        onSelect={() => {}}
        onCreate={() => {}}
      />,
    );

    expect(await screen.findByText('(untitled)')).toBeInTheDocument();
  });

  it('refetches when refreshToken bumps', async () => {
    const mock = installFetchMock([
      { ok: true, body: { conversations: [] } },
      {
        ok: true,
        body: {
          conversations: [
            {
              id: 'conv-after-bump',
              title: 'New conv',
              last_focus_pid: '',
              updated_at: 0,
              created_at: 0,
            },
          ],
        },
      },
    ]);

    const { rerender } = render(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={0}
        onSelect={() => {}}
        onCreate={() => {}}
      />,
    );

    await screen.findByText(/No conversations yet/i);
    expect(countConversationFetches(mock)).toBe(1);

    rerender(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={1}
        onSelect={() => {}}
        onCreate={() => {}}
      />,
    );

    expect(await screen.findByText('New conv')).toBeInTheDocument();
    expect(countConversationFetches(mock)).toBe(2);
  });

  it('shows the error state when /conversations fails', async () => {
    installFetchMock([{ ok: false, body: {} }]);

    render(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={0}
        onSelect={() => {}}
        onCreate={() => {}}
      />,
    );

    expect(
      await screen.findByText(/Couldn’t load conversations/i),
    ).toBeInTheDocument();
  });

  it('renders the care team roster in a collapsible section', async () => {
    const mock = installFetchMock(
      [{ ok: true, body: { conversations: [] } }],
      {
        user_id: 42,
        patients: [
          {
            patient_id: 'fixture-1',
            given_name: 'Eduardo',
            family_name: 'Perez',
            birth_date: '1958-03-12',
            last_admission: null,
            room: '4B',
          },
        ],
      },
    );

    render(
      <ConversationSidebar
        activeConversationId={null}
        refreshToken={0}
        onSelect={() => {}}
        onCreate={() => {}}
      />,
    );

    expect(await screen.findByText('Perez, Eduardo')).toBeInTheDocument();

    const toggle = screen.getByRole('button', { name: /Care team/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('Perez, Eduardo')).not.toBeVisible();

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Perez, Eduardo')).toBeVisible();
    expect(countPanelFetches(mock)).toBe(1);
  });
});
