/**
 * Conversation sidebar API.
 *
 * Wraps GET/POST /conversations for the sidebar list and "+" button, and
 * GET /conversations/:id/messages for the reopen-an-old-thread path.
 *
 * All requests carry the session cookie via `credentials: 'include'`.
 */

import { resolveAgentUrl } from './client';
import type { Block, ChatDiagnostics, ChatRoute } from './types';
import { parseBlock, parseDiagnostics, parseRoute } from './types';

export interface ConversationRow {
  readonly id: string;
  readonly title: string;
  readonly last_focus_pid: string;
  readonly updated_at: number;
  readonly created_at: number;
}

export interface ConversationListResponse {
  readonly conversations: readonly ConversationRow[];
}

export interface ConversationMessagesResponse {
  readonly id: string;
  readonly title: string;
  readonly last_focus_pid: string;
  readonly messages: readonly ConversationMessage[];
}

/**
 * One rehydrated turn. Issue 045: assistant rows may carry structured
 * provenance (``block``, ``route``, ``diagnostics``) when the per-turn
 * provenance store has a record for the conversation. Legacy rows from
 * the LangGraph checkpoint fallback have only ``role`` and ``content``;
 * the rehydration path renders those as ``plain`` blocks with no source
 * chips or route badge.
 */
export interface ConversationMessage {
  readonly role: 'user' | 'agent';
  readonly content: string;
  readonly block?: Block;
  readonly route?: ChatRoute;
  readonly diagnostics?: ChatDiagnostics;
  readonly workflow_id?: string;
  readonly classifier_confidence?: number;
}

function parseConversationMessage(raw: unknown): ConversationMessage | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  const role = obj.role;
  const content = obj.content;
  if (role !== 'user' && role !== 'agent') return null;
  if (typeof content !== 'string') return null;
  const message: {
    role: 'user' | 'agent';
    content: string;
    block?: Block;
    route?: ChatRoute;
    diagnostics?: ChatDiagnostics;
    workflow_id?: string;
    classifier_confidence?: number;
  } = { role, content };
  // Structured fields are optional. We swallow parse failures so a
  // single malformed turn doesn't blank the whole conversation — the UI
  // falls back to a plain block for that turn.
  if (role === 'agent') {
    if (obj.block !== undefined && obj.block !== null) {
      try {
        message.block = parseBlock(obj.block);
      } catch {
        // legacy or malformed — fall back to plain rendering
      }
    }
    if (obj.route !== undefined && obj.route !== null) {
      try {
        message.route = parseRoute(obj.route, 'route');
      } catch {
        // ignore
      }
    }
    if (obj.diagnostics !== undefined && obj.diagnostics !== null) {
      try {
        message.diagnostics = parseDiagnostics(obj.diagnostics, 'diagnostics');
      } catch {
        // ignore
      }
    }
    if (typeof obj.workflow_id === 'string') {
      message.workflow_id = obj.workflow_id;
    }
    if (typeof obj.classifier_confidence === 'number') {
      message.classifier_confidence = obj.classifier_confidence;
    }
  }
  return message;
}

export function parseConversationMessagesResponse(
  raw: unknown,
): ConversationMessagesResponse | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  if (typeof obj.id !== 'string' || typeof obj.title !== 'string') {
    return null;
  }
  const lastFocusPid =
    typeof obj.last_focus_pid === 'string' ? obj.last_focus_pid : '';
  const rawMessages = Array.isArray(obj.messages) ? obj.messages : [];
  const messages: ConversationMessage[] = [];
  for (const r of rawMessages) {
    const msg = parseConversationMessage(r);
    if (msg !== null) messages.push(msg);
  }
  return {
    id: obj.id,
    title: obj.title,
    last_focus_pid: lastFocusPid,
    messages,
  };
}

export async function fetchConversations(
  baseUrl?: string,
): Promise<ConversationListResponse | null> {
  const url = `${baseUrl ?? resolveAgentUrl()}/conversations`;
  try {
    const resp = await fetch(url, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
    if (!resp.ok) return null;
    return (await resp.json()) as ConversationListResponse;
  } catch {
    return null;
  }
}

export async function createConversation(
  baseUrl?: string,
): Promise<{ readonly id: string } | null> {
  const url = `${baseUrl ?? resolveAgentUrl()}/conversations`;
  try {
    const resp = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
    if (!resp.ok) return null;
    return (await resp.json()) as { readonly id: string };
  } catch {
    return null;
  }
}

export async function fetchConversationMessages(
  conversationId: string,
  baseUrl?: string,
): Promise<ConversationMessagesResponse | null> {
  const url = `${baseUrl ?? resolveAgentUrl()}/conversations/${encodeURIComponent(
    conversationId,
  )}/messages`;
  try {
    const resp = await fetch(url, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
    if (!resp.ok) return null;
    const raw: unknown = await resp.json();
    return parseConversationMessagesResponse(raw);
  } catch {
    return null;
  }
}
