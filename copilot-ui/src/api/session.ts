/**
 * Session management for the standalone login flow.
 *
 * The agent backend holds the SMART tokens; the browser carries an HttpOnly
 * session cookie (`copilot_session`). This module checks session validity
 * via GET /me and exposes the result for the UI to decide between the login
 * screen and the app shell.
 */

import { resolveAgentUrl } from './client';

export interface MeResponse {
  readonly user_id: number;
  readonly display_name: string;
  readonly fhir_user: string;
}

export type SessionStatus =
  | { readonly state: 'loading' }
  | { readonly state: 'authenticated'; readonly user: MeResponse }
  | { readonly state: 'unauthenticated' };

/**
 * Check whether the browser has a valid session cookie by calling GET /me.
 * Returns the user info on success, or null on 401/error.
 */
export async function fetchMe(
  baseUrl?: string,
): Promise<MeResponse | null> {
  const url = `${baseUrl ?? resolveAgentUrl()}/me`;
  try {
    const resp = await fetch(url, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
    if (!resp.ok) return null;
    return (await resp.json()) as MeResponse;
  } catch {
    return null;
  }
}

/**
 * Build the login URL that redirects the user to the agent backend's
 * /auth/login endpoint, which in turn redirects to OpenEMR's authorize page.
 */
export function buildLoginUrl(baseUrl?: string): string {
  return `${baseUrl ?? resolveAgentUrl()}/auth/login`;
}

/**
 * POST /auth/logout to revoke the session. The cookie is cleared server-side.
 */
export async function logout(baseUrl?: string): Promise<void> {
  const url = `${baseUrl ?? resolveAgentUrl()}/auth/logout`;
  try {
    await fetch(url, {
      method: 'POST',
      credentials: 'include',
    });
  } catch {
    // Best-effort; cookie will expire naturally.
  }
}
