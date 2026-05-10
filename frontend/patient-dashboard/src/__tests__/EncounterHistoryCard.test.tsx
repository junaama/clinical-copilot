import { render, screen, waitFor } from '@testing-library/react';
import EncounterHistoryCard from '../components/EncounterHistoryCard';
import type { FhirBundle, FhirEncounter } from '../fhir-types';

const FHIR_BASE = '/openemr/apis/default/fhir/r4';
const PATIENT_UUID = 'test-uuid-123';
const WEB_ROOT = '/openemr';

const FIXTURE_BUNDLE: FhirBundle<FhirEncounter> = {
  resourceType: 'Bundle',
  type: 'searchset',
  entry: [
    {
      resource: {
        resourceType: 'Encounter',
        id: 'enc-1',
        status: 'finished',
        type: [{ text: 'Office Visit' }],
        period: {
          start: '2024-03-15T09:00:00Z',
          end: '2024-03-15T09:30:00Z',
        },
        reasonCode: [{ text: 'Hypertension follow-up' }],
      },
    },
    {
      resource: {
        resourceType: 'Encounter',
        id: 'enc-2',
        status: 'finished',
        type: [{ text: 'Lab Review' }],
        period: {
          start: '2024-02-10T14:00:00Z',
        },
      },
    },
  ],
};

const EMPTY_BUNDLE: FhirBundle<FhirEncounter> = {
  resourceType: 'Bundle',
  type: 'searchset',
};

function mockFetch(response: unknown): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(response),
  } as Response);
}

describe('EncounterHistoryCard', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders encounter items when data is loaded', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Office Visit')).toBeInTheDocument();
    });

    expect(screen.getByText('Lab Review')).toBeInTheDocument();
    expect(screen.getByText(/Hypertension follow-up/)).toBeInTheDocument();
  });

  it('does not render when bundle has no entries', async () => {
    mockFetch(EMPTY_BUNDLE);

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.queryByTestId('card-encounter-history')).not.toBeInTheDocument();
      expect(screen.queryByText(/no encounter history recorded/i)).not.toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows error state on fetch failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({}),
    } as Response);

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('renders an edit link pointing to the legacy encounters page', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Office Visit')).toBeInTheDocument();
    });

    const editLink = screen.getByRole('link', { name: /edit/i });
    expect(editLink.getAttribute('href')).toContain(WEB_ROOT);
  });

  it('renders encounter status badges', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Office Visit')).toBeInTheDocument();
    });

    const badges = screen.getAllByText('finished');
    expect(badges.length).toBe(2);
  });

  it('renders start dates for encounters', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Office Visit')).toBeInTheDocument();
    });

    expect(screen.getByText(/2024-03-15/)).toBeInTheDocument();
    expect(screen.getByText(/2024-02-10/)).toBeInTheDocument();
  });

  it('fetches the correct FHIR Encounter endpoint', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<EncounterHistoryCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Office Visit')).toBeInTheDocument();
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      `${FHIR_BASE}/Encounter?patient=${PATIENT_UUID}`,
      expect.objectContaining({
        credentials: 'same-origin',
        headers: { Accept: 'application/fhir+json' },
      }),
    );
  });
});
