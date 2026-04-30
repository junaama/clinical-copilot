import { describe, expect, it, vi } from 'vitest';
import { sendChat } from '../api/client';
import { MOCK_TRIAGE_RESPONSE } from '../fixtures/mockData';

const baseRequest = {
  conversation_id: 'c1',
  patient_id: '4',
  user_id: 'naama',
  message: 'Who needs attention first?',
  smart_access_token: 'Bearer xyz',
} as const;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('sendChat', () => {
  it('returns ok=true and a parsed response on 200', async () => {
    const fetcher = vi.fn(async () => jsonResponse(MOCK_TRIAGE_RESPONSE));
    const result = await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: '',
    });
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error('unreachable');
    expect(result.response.block.kind).toBe('triage');
    expect(fetcher).toHaveBeenCalledWith(
      '/chat',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('serialises the request body as JSON', async () => {
    const fetcher = vi.fn(async () => jsonResponse(MOCK_TRIAGE_RESPONSE));
    await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: '',
    });
    const calls = fetcher.mock.calls as unknown as ReadonlyArray<readonly [unknown, RequestInit | undefined]>;
    const init = calls[0]?.[1];
    expect(init?.body).toBe(JSON.stringify(baseRequest));
  });

  it('surfaces the detail field on 4xx errors', async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({ detail: 'patient_context_mismatch' }, 403),
    );
    const result = await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: '',
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('unreachable');
    expect(result.status).toBe(403);
    expect(result.detail).toBe('patient_context_mismatch');
  });

  it('returns a network error when fetch throws', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('network down');
    });
    const result = await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: '',
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('unreachable');
    expect(result.status).toBe(0);
    expect(result.detail).toMatch(/network down/);
  });

  it('returns an error when the response JSON is malformed', async () => {
    const fetcher = vi.fn(
      async () =>
        new Response('not json', {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );
    const result = await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: '',
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('unreachable');
    expect(result.detail).toMatch(/invalid JSON/);
  });

  it('returns an error when the response shape is wrong', async () => {
    const fetcher = vi.fn(async () => jsonResponse({ unexpected: true }));
    const result = await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: '',
    });
    expect(result.ok).toBe(false);
  });

  it('uses the provided baseUrl', async () => {
    const fetcher = vi.fn(async () => jsonResponse(MOCK_TRIAGE_RESPONSE));
    await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'https://agent.example.com',
    });
    expect(fetcher).toHaveBeenCalledWith(
      'https://agent.example.com/chat',
      expect.anything(),
    );
  });

  it('handles non-JSON error bodies', async () => {
    const fetcher = vi.fn(
      async () => new Response('plain text error', { status: 500 }),
    );
    const result = await sendChat({
      request: baseRequest,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: '',
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error('unreachable');
    expect(result.status).toBe(500);
    expect(result.detail).toMatch(/plain text error/);
  });
});
