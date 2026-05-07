import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LoginPage } from '../components/LoginPage';
import { buildLoginUrl } from '../api/session';

describe('LoginPage consent explanation (issue 047)', () => {
  it('renders an app-specific consent section before the OpenEMR handoff (AC1)', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    expect(consent).toBeInTheDocument();
    // The consent section is rendered before the login button in the DOM so
    // the user reads it before continuing into OpenEMR's authorize page.
    const loginBtn = screen.getByRole('link', { name: /log in with openemr/i });
    const order = consent.compareDocumentPosition(loginBtn);
    // DOCUMENT_POSITION_FOLLOWING = 4 — consent precedes the login button.
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('explains broad read scopes for chart workflows (AC2)', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    expect(consent.textContent ?? '').toMatch(/chart/i);
  });

  it('explains broad read scopes for panel workflows (AC2)', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    expect(consent.textContent ?? '').toMatch(/panel/i);
  });

  it('explains broad read scopes for guideline and document workflows (AC2)', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    const text = consent.textContent ?? '';
    expect(text).toMatch(/guideline/i);
    expect(text).toMatch(/document/i);
  });

  it('explains source-grounding workflow purpose (AC2)', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    expect(consent.textContent ?? '').toMatch(/source/i);
  });

  it('explains offline access duration and purpose for this deployment (AC3)', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    expect(consent.textContent ?? '').toMatch(/offline access/i);
  });

  it('clearly states the current read-only posture (AC4)', () => {
    render(<LoginPage />);
    const consent = screen.getByTestId('login-consent');
    expect(consent.textContent ?? '').toMatch(/read-only/i);
  });

  it('still links to or continues through OpenEMR authorization (AC5)', () => {
    render(<LoginPage />);
    const link = screen.getByRole('link', { name: /log in with openemr/i });
    expect(link).toHaveAttribute('href', buildLoginUrl());
  });
});
