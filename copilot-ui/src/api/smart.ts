/**
 * SMART-on-FHIR launch parameter handling.
 *
 * The full PKCE handshake — exchanging `launch` for an access token via the
 * FHIR server's authorization endpoint — is a backend concern (the agent
 * service holds the client secret and the redirect URI). The frontend's job is
 * to read the launch params off the URL on first paint and pass them along to
 * the agent so the agent knows which launch context to use.
 *
 * For the demo path the launching OpenEMR may pass an `access_token` directly
 * via the URL fragment (a non-standard shortcut for fixture mode); we accept
 * that and surface it as `smart_access_token`.
 */

export interface SmartLaunchContext {
  /** FHIR server base URL — the `iss` parameter from the SMART launch redirect. */
  readonly iss: string | null;
  /** Opaque launch token to be exchanged for an access token. */
  readonly launch: string | null;
  /** Patient ID extracted from launch context (when available). */
  readonly patientId: string | null;
  /** User ID extracted from launch context (when available). */
  readonly userId: string | null;
  /** Bearer access token, if already exchanged or passed by the host. */
  readonly accessToken: string;
}

const EMPTY_CONTEXT: SmartLaunchContext = {
  iss: null,
  launch: null,
  patientId: null,
  userId: null,
  accessToken: '',
};

/**
 * Read SMART launch params from a URL. Searches both the query string and the
 * hash fragment so that token-via-fragment redirects still parse.
 */
export function parseSmartLaunch(href: string): SmartLaunchContext {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return EMPTY_CONTEXT;
  }

  const query = url.searchParams;
  const hash = parseHashParams(url.hash);

  const get = (key: string): string | null => query.get(key) ?? hash.get(key) ?? null;

  return {
    iss: get('iss'),
    launch: get('launch'),
    patientId: get('patient') ?? get('patient_id'),
    userId: get('user') ?? get('user_id'),
    accessToken: get('access_token') ?? '',
  };
}

function parseHashParams(hash: string): Map<string, string> {
  const out = new Map<string, string>();
  if (hash.length <= 1) return out;
  const trimmed = hash.startsWith('#') ? hash.slice(1) : hash;
  for (const part of trimmed.split('&')) {
    if (part.length === 0) continue;
    const eq = part.indexOf('=');
    const key = eq === -1 ? part : part.slice(0, eq);
    const value = eq === -1 ? '' : decodeURIComponent(part.slice(eq + 1));
    out.set(decodeURIComponent(key), value);
  }
  return out;
}

/**
 * TODO(backend handshake): exchange `launch` + `iss` for a SMART access token
 * via PKCE. Currently the agent holds this responsibility — the UI only forwards
 * the launch params it received. Implement here when we move to direct
 * authorization-code flow on the frontend.
 *
 * Signature kept stable so callers can wire it without churn.
 */
export async function exchangeLaunchForToken(
  _ctx: SmartLaunchContext,
): Promise<{ accessToken: string }> {
  return Promise.reject(
    new Error('exchangeLaunchForToken not implemented — backend handles SMART exchange in v1'),
  );
}
