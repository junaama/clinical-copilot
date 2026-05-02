/**
 * Conversation sidebar API.
 *
 * Wraps GET/POST /conversations for the sidebar list and "+" button, and
 * GET /conversations/:id/messages for the reopen-an-old-thread path.
 *
 * All requests carry the session cookie via `credentials: 'include'`.
 */

import { resolveAgentUrl } from './client';

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

export interface ConversationMessage {
  readonly role: 'user' | 'agent';
  readonly content: string;
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
    return (await resp.json()) as ConversationMessagesResponse;
  } catch {
    return null;
  }
}
