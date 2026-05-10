import { render, screen, waitFor } from '@testing-library/react';
import AllergyCard from '../components/AllergyCard';
import type { FhirBundle, FhirAllergyIntolerance } from '../fhir-types';

const FHIR_BASE = '/openemr/apis/default/fhir/r4';
const PATIENT_UUID = 'test-uuid-123';
const WEB_ROOT = '/openemr';

const FIXTURE_BUNDLE: FhirBundle<FhirAllergyIntolerance> = {
  resourceType: 'Bundle',
  type: 'searchset',
  entry: [
    {
      resource: {
        resourceType: 'AllergyIntolerance',
        id: 'a1',
        code: { text: 'Penicillin' },
        clinicalStatus: { coding: [{ code: 'active' }] },
        category: ['medication'],
        criticality: 'high',
        recordedDate: '2024-01-15',
        reaction: [{ manifestation: [{ text: 'Hives' }] }],
      },
    },
    {
      resource: {
        resourceType: 'AllergyIntolerance',
        id: 'a2',
        code: { text: 'Peanuts (substance)' },
        clinicalStatus: { coding: [{ code: 'active' }] },
        category: ['food'],
        criticality: 'low',
      },
    },
  ],
};

const EMPTY_BUNDLE: FhirBundle<FhirAllergyIntolerance> = {
  resourceType: 'Bundle',
  type: 'searchset',
};

function mockFetch(response: unknown): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(response),
  } as Response);
}

describe('AllergyCard', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders allergy items when data is loaded', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<AllergyCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Penicillin')).toBeInTheDocument();
    });

    expect(screen.getByText('Peanuts')).toBeInTheDocument();
    expect(screen.getByText('substance')).toHaveClass('clinical-card__badge--qualifier');
    // "Hives" is inside "Reaction: Hives" — use substring matcher
    expect(screen.getByText(/Hives/)).toBeInTheDocument();
  });

  it('does not render when bundle has no entries', async () => {
    mockFetch(EMPTY_BUNDLE);

    render(<AllergyCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.queryByTestId('card-allergies')).not.toBeInTheDocument();
      expect(screen.queryByText(/no allergies recorded/i)).not.toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));

    render(<AllergyCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows error state on fetch failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({}),
    } as Response);

    render(<AllergyCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('renders an edit link pointing to the legacy allergy page', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<AllergyCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Penicillin')).toBeInTheDocument();
    });

    const editLink = screen.getByRole('link', { name: /edit/i });
    expect(editLink.getAttribute('href')).toContain(WEB_ROOT);
  });
});
