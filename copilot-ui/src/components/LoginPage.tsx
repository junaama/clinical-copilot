/**
 * Login page for the standalone flow.
 *
 * Shows a simple login button that redirects the user to the agent backend's
 * /auth/login endpoint, which initiates the SMART-on-FHIR PKCE flow against
 * OpenEMR's authorize endpoint.
 */

import { type JSX } from 'react';
import { buildLoginUrl } from '../api/session';

export function LoginPage(): JSX.Element {
  return (
    <div className="login-page">
      <div className="login-page__card">
        <h1 className="login-page__title">Clinical Co-Pilot</h1>
        <p className="login-page__subtitle">
          Sign in with your OpenEMR credentials to access your patient panel.
        </p>
        <a href={buildLoginUrl()} className="login-page__btn">
          Log in with OpenEMR
        </a>
      </div>
    </div>
  );
}
