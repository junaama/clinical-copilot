/**
 * Full-screen application shell for the standalone login flow.
 *
 * Replaces the floating Launcher + AgentPanel layout when the user enters
 * via the standalone URL (no EHR-launch params). Contains:
 *
 * - A top bar with the user's display name and a logout button.
 * - The existing AgentPanel as the main conversation surface.
 *
 * The sidebar for multi-conversation support is deferred to issue 004.
 */

import { type JSX } from 'react';
import { logout } from '../api/session';
import type { MeResponse } from '../hooks/useSession';

interface AppShellProps {
  readonly user: MeResponse;
  readonly children: React.ReactNode;
}

export function AppShell({ user, children }: AppShellProps): JSX.Element {
  const handleLogout = (): void => {
    logout().then(() => {
      window.location.reload();
    });
  };

  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <div className="app-shell__brand">Clinical Co-Pilot</div>
        <div className="app-shell__user">
          <span className="app-shell__user-name">{user.display_name}</span>
          <button
            type="button"
            className="app-shell__logout-btn"
            onClick={handleLogout}
          >
            Log out
          </button>
        </div>
      </header>
      <main className="app-shell__body">{children}</main>
    </div>
  );
}
