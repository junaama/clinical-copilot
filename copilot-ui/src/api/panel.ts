/**
 * GET /panel — the authenticated user's CareTeam roster.
 *
 * Powers the empty-state PanelView in the standalone shell. Returns the
 * subset for non-admin users (e.g. dr_smith) and the full set for users in
 * the configured admin allow-list. The session cookie travels via
 * `credentials: 'include'`.
 */

import { resolveAgentUrl } from './client';

export interface PanelPatient {
  readonly patient_id: string;
  readonly given_name: string;
  readonly family_name: string;
  readonly birth_date: string;
  readonly last_admission: string | null;
  readonly room: string | null;
}

export interface PanelResponse {
  readonly user_id: number;
  readonly patients: readonly PanelPatient[];
}

export async function fetchPanel(baseUrl?: string): Promise<PanelResponse | null> {
  const url = `${baseUrl ?? resolveAgentUrl()}/panel`;
  try {
    const resp = await fetch(url, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
    if (!resp.ok) return null;
    return (await resp.json()) as PanelResponse;
  } catch {
    return null;
  }
}
