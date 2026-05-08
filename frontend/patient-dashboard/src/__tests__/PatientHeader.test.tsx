import { render, screen, waitFor } from '@testing-library/react';
import PatientHeader from '../components/PatientHeader';
import type { FhirPatient } from '../fhir-types';

const FHIR_BASE = '/openemr/apis/default/fhir';
const PATIENT_UUID = '90cfdaa2-60ea-4b20-a6d9-1cf01aaaaabb';

/** Complete FHIR Patient fixture mimicking OpenEMR response. */
const FIXTURE_PATIENT: FhirPatient = {
  resourceType: 'Patient',
  id: PATIENT_UUID,
  active: true,
  name: [
    {
      use: 'official',
      family: 'Chen',
      given: ['Eduardo', 'Miguel'],
      prefix: ['Mr.'],
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

/** Minimal FHIR Patient with sparse data. */
const SPARSE_PATIENT: FhirPatient = {
  resourceType: 'Patient',
  name: [{ family: 'Whitaker' }],
  gender: 'female',
};

function mockFetchSuccess(data: FhirPatient): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    json: () => Promise.resolve(data),
  } as Response);
}

function mockFetchError(status: number): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: false,
    status,
    json: () => Promise.resolve({}),
  } as Response);
}

function mockFetchNetworkError(): void {
  vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Network error'));
}

describe('PatientHeader', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows loading state initially', () => {
    // Never-resolving fetch to keep loading state
    vi.spyOn(globalThis, 'fetch').mockReturnValueOnce(new Promise(() => {}));

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    expect(screen.getByTestId('patient-header-loading')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Loading patient information');
  });

  it('renders patient identity fields from complete FHIR data', async () => {
    mockFetchSuccess(FIXTURE_PATIENT);

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-header')).toBeInTheDocument();
    });

    expect(screen.getByTestId('patient-name')).toHaveTextContent('Mr. Eduardo Miguel Chen');
    expect(screen.getByTestId('patient-dob')).toHaveTextContent('1965-04-23');
    expect(screen.getByTestId('patient-sex')).toHaveTextContent('Male');
    expect(screen.getByTestId('patient-mrn')).toHaveTextContent('MRN-10042');
    expect(screen.getByTestId('patient-status')).toHaveTextContent('Active');
  });

  it('renders sparse patient data with unknown fallbacks', async () => {
    mockFetchSuccess(SPARSE_PATIENT);

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-header')).toBeInTheDocument();
    });

    expect(screen.getByTestId('patient-name')).toHaveTextContent('Whitaker');
    expect(screen.getByTestId('patient-dob')).toHaveTextContent('Unknown');
    expect(screen.getByTestId('patient-sex')).toHaveTextContent('Female');
    expect(screen.getByTestId('patient-mrn')).toHaveTextContent('Unknown');
    expect(screen.getByTestId('patient-status')).toHaveTextContent('Inactive');
  });

  it('renders error state on HTTP failure', async () => {
    mockFetchError(404);

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-header-error')).toBeInTheDocument();
    });

    expect(screen.getByRole('alert')).toHaveTextContent('Unable to load patient information');
    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 404');
  });

  it('renders error state on network failure', async () => {
    mockFetchNetworkError();

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-header-error')).toBeInTheDocument();
    });

    expect(screen.getByRole('alert')).toHaveTextContent('Network error');
  });

  it('renders error state on invalid response shape', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ resourceType: 'Observation' }),
    } as Response);

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-header-error')).toBeInTheDocument();
    });

    expect(screen.getByRole('alert')).toHaveTextContent('Invalid FHIR Patient response');
  });

  it('fetches the correct FHIR URL', async () => {
    mockFetchSuccess(FIXTURE_PATIENT);

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-header')).toBeInTheDocument();
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      `${FHIR_BASE}/Patient/${PATIENT_UUID}`,
      expect.objectContaining({
        credentials: 'same-origin',
        headers: { Accept: 'application/fhir+json' },
      }),
    );
  });

  it('applies active status class', async () => {
    mockFetchSuccess(FIXTURE_PATIENT);

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-status')).toBeInTheDocument();
    });

    expect(screen.getByTestId('patient-status')).toHaveClass('patient-header__status--active');
  });

  it('applies inactive status class', async () => {
    mockFetchSuccess({ ...FIXTURE_PATIENT, active: false });

    render(<PatientHeader fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} />);

    await waitFor(() => {
      expect(screen.getByTestId('patient-status')).toBeInTheDocument();
    });

    expect(screen.getByTestId('patient-status')).toHaveClass('patient-header__status--inactive');
  });
});
