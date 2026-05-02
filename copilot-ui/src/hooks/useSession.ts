/**
 * React hook for standalone session detection.
 *
 * On mount, calls GET /me (credentials: 'include'). If 200, the user is
 * authenticated and the hook returns their info. If 401, the hook returns
 * 'unauthenticated' so the caller can render the login screen.
 */

import { useEffect, useState } from 'react';
import { fetchMe, type MeResponse, type SessionStatus } from '../api/session';

export function useSession(): SessionStatus {
  const [status, setStatus] = useState<SessionStatus>({ state: 'loading' });

  useEffect(() => {
    let cancelled = false;

    fetchMe().then((user) => {
      if (cancelled) return;
      if (user) {
        setStatus({ state: 'authenticated', user });
      } else {
        setStatus({ state: 'unauthenticated' });
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return status;
}

export type { MeResponse, SessionStatus };
