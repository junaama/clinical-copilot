import { render, screen, waitFor } from '@testing-library/react';
import ProblemListCard from '../components/ProblemListCard';
import type { FhirBundle, FhirCondition } from '../fhir-types';

const FHIR_BASE = '/openemr/apis/default/fhir/r4';
const PATIENT_UUID = 'test-uuid-123';
const WEB_ROOT = '/openemr';

const FIXTURE_BUNDLE: FhirBundle<FhirCondition> = {
  resourceType: 'Bundle',
  type: 'searchset',
  entry: [
    {
      resource: {
        resourceType: 'Condition',
        id: 'c1',
        code: { text: 'Type 2 Diabetes Mellitus (finding)' },
        clinicalStatus: { coding: [{ code: 'active' }] },
        onsetDateTime: '2020-03-10',
      },
    },
    {
      resource: {
        resourceType: 'Condition',
        id: 'c2',
        code: { text: 'Hypertension' },
        clinicalStatus: { coding: [{ code: 'active' }] },
      },
    },
  ],
};

const EMPTY_BUNDLE: FhirBundle<FhirCondition> = {
  resourceType: 'Bundle',
  type: 'searchset',
};

function mockFetch(response: unknown): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(response),
  } as Response);
}

describe('ProblemListCard', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders problem items when data is loaded', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<ProblemListCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Type 2 Diabetes Mellitus')).toBeInTheDocument();
    });
    expect(screen.getByText('finding')).toHaveClass('clinical-card__badge--qualifier');
    expect(screen.getByText('Hypertension')).toBeInTheDocument();
  });

  it('does not render when bundle has no entries', async () => {
    mockFetch(EMPTY_BUNDLE);

    render(<ProblemListCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.queryByTestId('card-problem-list')).not.toBeInTheDocument();
      expect(screen.queryByText(/no problem list recorded/i)).not.toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));

    render(<ProblemListCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows error state on fetch failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({}),
    } as Response);

    render(<ProblemListCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('renders edit link', async () => {
    mockFetch(FIXTURE_BUNDLE);

    render(<ProblemListCard fhirBaseUrl={FHIR_BASE} patientUuid={PATIENT_UUID} webRoot={WEB_ROOT} />);

    await waitFor(() => {
      expect(screen.getByText('Type 2 Diabetes Mellitus')).toBeInTheDocument();
    });

    const editLink = screen.getByRole('link', { name: /edit/i });
    expect(editLink.getAttribute('href')).toContain(WEB_ROOT);
  });
});
