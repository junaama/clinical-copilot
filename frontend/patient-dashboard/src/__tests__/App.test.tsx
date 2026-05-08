import { render, screen, waitFor } from '@testing-library/react';
import App from '../App';
import type { PatientDashboardConfig } from '../types';
import type { FhirPatient, FhirBundle } from '../fhir-types';

const FIXTURE_CONFIG: PatientDashboardConfig = {
  pid: 1,
  patientUuid: '90cfdaa2-60ea-4b20-a6d9-1cf01aaaaabb',
  webRoot: '/openemr',
  fhirBaseUrl: '/openemr/apis/default/fhir/r4',
  legacyDashboardUrl: '/openemr/interface/patient_file/summary/demographics_legacy.php',
  modernDashboardUrl: '/openemr/interface/patient_file/summary/demographics.php',
  csrfToken: 'test-csrf-token-abc123',
};

const FIXTURE_PATIENT: FhirPatient = {
  resourceType: 'Patient',
  id: '90cfdaa2-60ea-4b20-a6d9-1cf01aaaaabb',
  active: true,
  name: [
    {
      use: 'official',
      family: 'Chen',
      given: ['Eduardo'],
    },
  ],
  birthDate: '1965-04-23',
  gender: 'male',
  identifier: [
    {
      use: 'official',
      type: { coding: [{ code: 'SS' }] },
      value: 'MRN-10042',
    },
  ],
};

const EMPTY_BUNDLE: FhirBundle<unknown> = {
  resourceType: 'Bundle',
  type: 'searchset',
};

/** Route-aware fetch mock: Patient read → patient fixture, search → empty bundle. */
function mockFetchSuccess(): void {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();

    if (url.includes('/Patient/') && !url.includes('?')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(FIXTURE_PATIENT),
      } as Response);
    }

    // All search endpoints return empty bundles
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(EMPTY_BUNDLE),
    } as Response);
  });
}

describe('App', () => {
  afterEach(() => {
    delete window.__OPENEMR_PATIENT_DASHBOARD__;
    vi.restoreAllMocks();
  });

  it('renders error when config is missing', () => {
    render(<App />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Patient dashboard configuration is missing',
    );
  });

  it('renders the dashboard when config is present', async () => {
    window.__OPENEMR_PATIENT_DASHBOARD__ = FIXTURE_CONFIG;
    mockFetchSuccess();

    render(<App />);
    expect(screen.getByTestId('patient-dashboard')).toBeInTheDocument();

    // Wait for the patient header to load
    await waitFor(() => {
      expect(screen.getByTestId('patient-header')).toBeInTheDocument();
    });
  });

  it('renders a link to the legacy dashboard', async () => {
    window.__OPENEMR_PATIENT_DASHBOARD__ = FIXTURE_CONFIG;
    mockFetchSuccess();

    render(<App />);
    const link = screen.getByTestId('legacy-dashboard-link');
    expect(link).toHaveAttribute('href', FIXTURE_CONFIG.legacyDashboardUrl);
    expect(link).toHaveTextContent('View Legacy Dashboard');

    // Wait for async state updates to settle
    await waitFor(() => {
      expect(screen.getByTestId('patient-header')).toBeInTheDocument();
    });
  });

  it('renders the patient header with FHIR data', async () => {
    window.__OPENEMR_PATIENT_DASHBOARD__ = FIXTURE_CONFIG;
    mockFetchSuccess();

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-name')).toHaveTextContent('Eduardo Chen');
    });

    expect(screen.getByTestId('patient-dob')).toHaveTextContent('1965-04-23');
    expect(screen.getByTestId('patient-sex')).toHaveTextContent('Male');
    expect(screen.getByTestId('patient-mrn')).toHaveTextContent('MRN-10042');
  });

  it('renders the five clinical cards including encounter history', async () => {
    window.__OPENEMR_PATIENT_DASHBOARD__ = FIXTURE_CONFIG;
    mockFetchSuccess();

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('card-allergies')).toBeInTheDocument();
    });

    expect(screen.getByTestId('card-problem-list')).toBeInTheDocument();
    expect(screen.getByTestId('card-medications')).toBeInTheDocument();
    expect(screen.getByTestId('card-prescriptions')).toBeInTheDocument();
    expect(screen.getByTestId('card-encounter-history')).toBeInTheDocument();
  });
});
