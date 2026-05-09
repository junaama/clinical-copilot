import { renderHook, waitFor } from '@testing-library/react';
import { useFhirSearch } from '../hooks/use-fhir-search';
import type { FhirBundle, FhirAllergyIntolerance } from '../fhir-types';

const FHIR_BASE = '/openemr/apis/default/fhir/r4';
const PATIENT_UUID = 'test-uuid-123';
const RESOURCE_PATH = 'AllergyIntolerance';

const FIXTURE_BUNDLE: FhirBundle<FhirAllergyIntolerance> = {
  resourceType: 'Bundle',
  type: 'searchset',
  entry: [
    {
      resource: {
        resourceType: 'AllergyIntolerance',
        id: 'a1',
        code: { text: 'Penicillin' },
      },
    },
  ],
};

describe('useFhirSearch', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches a FHIR search bundle and returns it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(FIXTURE_BUNDLE),
    } as Response);

    const { result } = renderHook(() =>
      useFhirSearch<FhirAllergyIntolerance>(FHIR_BASE, RESOURCE_PATH, PATIENT_UUID),
    );

    // Initially loading
    expect(result.current.loading).toBe(true);
    expect(result.current.bundle).toBeNull();

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.bundle).toEqual(FIXTURE_BUNDLE);
    expect(result.current.error).toBeNull();

    // Verify correct URL
    expect(globalThis.fetch).toHaveBeenCalledWith(
      `${FHIR_BASE}/${RESOURCE_PATH}?patient=${PATIENT_UUID}`,
      expect.objectContaining({
        credentials: 'same-origin',
        headers: { Accept: 'application/fhir+json' },
      }),
    );
  });

  it('returns error on HTTP failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    } as Response);

    const { result } = renderHook(() =>
      useFhirSearch<FhirAllergyIntolerance>(FHIR_BASE, RESOURCE_PATH, PATIENT_UUID),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toContain('404');
    expect(result.current.bundle).toBeNull();
  });

  it('returns error on network failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));

    const { result } = renderHook(() =>
      useFhirSearch<FhirAllergyIntolerance>(FHIR_BASE, RESOURCE_PATH, PATIENT_UUID),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe('Failed to fetch');
    expect(result.current.bundle).toBeNull();
  });

  it('validates response is a Bundle', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ resourceType: 'Patient' }),
    } as Response);

    const { result } = renderHook(() =>
      useFhirSearch<FhirAllergyIntolerance>(FHIR_BASE, RESOURCE_PATH, PATIENT_UUID),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toContain('Invalid FHIR Bundle');
    expect(result.current.bundle).toBeNull();
  });
});
