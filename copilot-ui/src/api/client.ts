/**
 * Thin client for POST /chat. Uses the global fetch — no axios — and parses
 * the response through types.ts so that anything past this boundary is typed.
 */

import { parseChatResponse, type ChatRequest, type ChatResponse } from './types';

/** Resolves the base URL for the agent service. Empty string ⇒ same-origin /api proxy. */
export function resolveAgentUrl(): string {
  // VITE_AGENT_URL is the deployed agent URL; in dev it's blank and we use the
  // vite proxy at /api → http://localhost:8000.
  const fromEnv = import.meta.env.VITE_AGENT_URL ?? '';
  return typeof fromEnv === 'string' ? fromEnv.replace(/\/$/, '') : '';
}

/** Result envelope — error path is explicit, never thrown back into render. */
export type ChatResult =
  | { readonly ok: true; readonly response: ChatResponse }
  | { readonly ok: false; readonly status: number; readonly detail: string };

interface SendChatOptions {
  readonly request: ChatRequest;
  readonly signal?: AbortSignal;
  readonly fetcher?: typeof fetch;
  readonly baseUrl?: string;
}

/**
 * POST /chat. Always resolves; never rejects. Caller renders the error envelope
 * inside the agent bubble per CLAUDE.md error-handling rules.
 */
export async function sendChat(opts: SendChatOptions): Promise<ChatResult> {
  const fetcher = opts.fetcher ?? fetch;
  const baseUrl = opts.baseUrl ?? resolveAgentUrl();
  const url = `${baseUrl}/chat`;

  let resp: Response;
  try {
    resp = await fetcher(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify(opts.request),
      signal: opts.signal,
    });
  } catch (error: unknown) {
    return {
      ok: false,
      status: 0,
      detail: error instanceof Error ? error.message : 'network error',
    };
  }

  let bodyText: string;
  try {
    bodyText = await resp.text();
  } catch {
    return { ok: false, status: resp.status, detail: 'response body unreadable' };
  }

  if (!resp.ok) {
    const detail = extractDetail(bodyText) ?? `HTTP ${resp.status}`;
    return { ok: false, status: resp.status, detail };
  }

  let parsedJson: unknown;
  try {
    parsedJson = JSON.parse(bodyText);
  } catch {
    return { ok: false, status: resp.status, detail: 'invalid JSON in response' };
  }

  try {
    return { ok: true, response: parseChatResponse(parsedJson) };
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : 'malformed response';
    return { ok: false, status: resp.status, detail };
  }
}

function extractDetail(bodyText: string): string | null {
  if (bodyText.length === 0) return null;
  try {
    const obj: unknown = JSON.parse(bodyText);
    if (typeof obj === 'object' && obj !== null && 'detail' in obj) {
      const detail = (obj as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
    }
  } catch {
    // fall through: treat as plain text
  }
  return bodyText.slice(0, 500);
}
