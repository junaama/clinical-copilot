import { render, screen, waitFor } from '@testing-library/react';
import MedicationCard from '../components/MedicationCard';
import type { FhirBundle, FhirMedicationRequest } from '../fhir-types';

const FHIR_BASE = '/openemr/apis/default/fhir/r4';
const PATIENT_UUID = 'test-uuid-123';
const WEB_ROOT = '/openemr';

const FIXTURE_BUNDLE: FhirBundle<FhirMedicationRequest> = {
  resourceType: 'Bundle',
  type: 'searchset',
  entry: [
    {
      resource: {
        resourceType: 'MedicationRequest',
        id: 'm1',
        status: 'active',
        intent: 'order',
        medicationCodeableConcept: { text: 'Metformin 500mg tablet' },
        dosageInstruction: [{ text: '1 tablet twice daily' }],
        requester: { display: 'Dr. Lopez' },
        authoredOn: '2024-06-01',
      },
    },
    {
      resource: {
        resourceType: 'MedicationRequest',
        id: 'm2',
        status: 'active',
        intent: 'order',
        medicationCodeableConcept: { text: 'Lisinopril 10mg' },
        dosageInstruction: [{ text: '1 tablet daily' }],
      },
    },
  ],
};

const EMPTY_BUNDLE: FhirBundle<FhirMedicationRequest> = {
  resourceType: 'Bundle',
  type: 'searchset',
};

function mockFetch(response: unknown): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(response),
  } as Response);
}

describe('MedicationCard', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders medication items when data is loaded', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(
      <MedicationCard
        title="Medications"
        fhirBaseUrl={FHIR_BASE}
        patientUuid={PATIENT_UUID}
        webRoot={WEB_ROOT}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Metformin 500mg tablet')).toBeInTheDocument();
    });
    expect(screen.getByText('Lisinopril 10mg')).toBeInTheDocument();
  });

  it('shows empty state when bundle has no entries', async () => {
    mockFetch(EMPTY_BUNDLE);

    render(
      <MedicationCard
        title="Medications"
        fhirBaseUrl={FHIR_BASE}
        patientUuid={PATIENT_UUID}
        webRoot={WEB_ROOT}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/no medications recorded/i)).toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));

    render(
      <MedicationCard
        title="Medications"
        fhirBaseUrl={FHIR_BASE}
        patientUuid={PATIENT_UUID}
        webRoot={WEB_ROOT}
      />,
    );

    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows error state on fetch failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({}),
    } as Response);

    render(
      <MedicationCard
        title="Medications"
        fhirBaseUrl={FHIR_BASE}
        patientUuid={PATIENT_UUID}
        webRoot={WEB_ROOT}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('renders with Prescriptions title variant', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(
      <MedicationCard
        title="Prescriptions"
        fhirBaseUrl={FHIR_BASE}
        patientUuid={PATIENT_UUID}
        webRoot={WEB_ROOT}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Metformin 500mg tablet')).toBeInTheDocument();
    });

    expect(screen.getByText('Prescriptions')).toBeInTheDocument();
  });

  it('renders edit link', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(
      <MedicationCard
        title="Medications"
        fhirBaseUrl={FHIR_BASE}
        patientUuid={PATIENT_UUID}
        webRoot={WEB_ROOT}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Metformin 500mg tablet')).toBeInTheDocument();
    });

    const editLink = screen.getByRole('link', { name: /edit/i });
    expect(editLink.getAttribute('href')).toContain(WEB_ROOT);
  });

  it('displays dosage and requester when available', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(
      <MedicationCard
        title="Medications"
        fhirBaseUrl={FHIR_BASE}
        patientUuid={PATIENT_UUID}
        webRoot={WEB_ROOT}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Metformin 500mg tablet')).toBeInTheDocument();
    });

    expect(screen.getByText(/1 tablet twice daily/)).toBeInTheDocument();
    expect(screen.getByText(/Dr. Lopez/)).toBeInTheDocument();
  });
});
