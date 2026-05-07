/**
 * Login page for the standalone flow.
 *
 * Shows a simple login button that redirects the user to the agent backend's
 * /auth/login endpoint, which initiates the SMART-on-FHIR PKCE flow against
 * OpenEMR's authorize endpoint.
 *
 * Issue 047: an app-specific consent explanation precedes the OpenEMR
 * authorization handoff. OpenEMR's own consent page asks for broad scopes
 * without product-level context — surfacing the Clinical Co-Pilot rationale
 * here lets the clinician understand why the app needs broad reads, why
 * offline access is requested in this deployment, and that the app remains
 * read-only.
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
        <section
          data-testid="login-consent"
          className="login-page__consent"
          aria-labelledby="login-consent-heading"
        >
          <h2 id="login-consent-heading" className="login-page__consent-title">
            Before you continue
          </h2>
          <p className="login-page__consent-lead">
            Clinical Co-Pilot will ask OpenEMR for permission to use your
            account for the following clinical workflows:
          </p>
          <ul className="login-page__consent-list">
            <li>
              <strong>Chart review:</strong> read the structured chart
              (problems, medications, vitals, labs, allergies, encounters) for
              patients on your CareTeam panel so the agent can summarize and
              answer questions about a patient.
            </li>
            <li>
              <strong>Panel triage:</strong> read your CareTeam panel and the
              members it lists so the agent can rank attention across multiple
              patients.
            </li>
            <li>
              <strong>Guideline and document evidence:</strong> read uploaded
              documents and reference indexed clinical guidelines so the agent
              can ground its answers in retrievable evidence.
            </li>
            <li>
              <strong>Source grounding:</strong> link every clinical claim
              back to its FHIR resource, uploaded document, or guideline
              citation so you can verify the answer&rsquo;s source.
            </li>
          </ul>
          <p className="login-page__consent-offline">
            <strong>Offline access</strong> is requested so a single
            conversation can stay open across the workday — the agent backend
            quietly refreshes its OpenEMR access token in the background so
            you do not have to re-authenticate mid-conversation. Your tokens
            never reach the browser. You can sign out at any time to revoke
            the session.
          </p>
          <p className="login-page__consent-readonly">
            <strong>This app is read-only.</strong> Clinical Co-Pilot does not
            order medications, write notes, or modify the chart. Any future
            write capability would require a separate, explicitly confirmed
            flow.
          </p>
        </section>
        <a href={buildLoginUrl()} className="login-page__btn">
          Log in with OpenEMR
        </a>
      </div>
    </div>
  );
}
