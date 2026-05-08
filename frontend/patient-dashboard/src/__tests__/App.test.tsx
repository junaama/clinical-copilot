import { render, screen } from '@testing-library/react';
import App from '../App';
import type { PatientDashboardConfig } from '../types';

const FIXTURE_CONFIG: PatientDashboardConfig = {
  pid: 1,
  patientUuid: '90cfdaa2-60ea-4b20-a6d9-1cf01aaaaabb',
  webRoot: '/openemr',
  fhirBaseUrl: '/openemr/apis/default/fhir/r4',
  legacyDashboardUrl: '/openemr/interface/patient_file/summary/demographics_legacy.php',
  modernDashboardUrl: '/openemr/interface/patient_file/summary/demographics.php',
  csrfToken: 'test-csrf-token-abc123',
};

describe('App', () => {
  afterEach(() => {
    delete window.__OPENEMR_PATIENT_DASHBOARD__;
  });

  it('renders error when config is missing', () => {
    render(<App />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Patient dashboard configuration is missing',
    );
  });

  it('renders the dashboard when config is present', () => {
    window.__OPENEMR_PATIENT_DASHBOARD__ = FIXTURE_CONFIG;
    render(<App />);
    expect(screen.getByTestId('patient-dashboard')).toBeInTheDocument();
    expect(screen.getByText(/Patient ID: 1/)).toBeInTheDocument();
  });

  it('renders a link to the legacy dashboard', () => {
    window.__OPENEMR_PATIENT_DASHBOARD__ = FIXTURE_CONFIG;
    render(<App />);
    const link = screen.getByTestId('legacy-dashboard-link');
    expect(link).toHaveAttribute('href', FIXTURE_CONFIG.legacyDashboardUrl);
    expect(link).toHaveTextContent('View Legacy Dashboard');
  });
});
