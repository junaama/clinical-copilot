/**
 * Generic React hook for fetching a FHIR search Bundle from the OpenEMR API.
 */

import { useEffect, useState } from 'react';
import type { FhirBundle } from '../fhir-types';
import { fhirRequestInit } from '../fhir-fetch';

export interface UseFhirSearchResult<T> {
  readonly bundle: FhirBundle<T> | null;
  readonly loading: boolean;
  readonly error: string | null;
}

/**
 * Fetch a FHIR search Bundle by resource type and patient UUID.
 *
 * @param fhirBaseUrl  - Base URL for the FHIR R4 API
 * @param resourceType - FHIR resource type (e.g. "AllergyIntolerance")
 * @param patientUuid  - FHIR-compatible patient UUID
 */
export function useFhirSearch<T>(
  fhirBaseUrl: string,
  resourceType: string,
  patientUuid: string,
): UseFhirSearchResult<T> {
  const [bundle, setBundle] = useState<FhirBundle<T> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchBundle(): Promise<void> {
      setLoading(true);
      setError(null);

      try {
        const url = `${fhirBaseUrl}/${resourceType}?patient=${encodeURIComponent(patientUuid)}`;
        const response = await fetch(url, fhirRequestInit());

        if (!response.ok) {
          throw new Error(`Failed to load ${resourceType} data (HTTP ${response.status})`);
        }

        const data: unknown = await response.json();

        if (cancelled) return;

        if (
          typeof data === 'object' &&
          data !== null &&
          'resourceType' in data &&
          (data as { resourceType: unknown }).resourceType === 'Bundle'
        ) {
          setBundle(data as FhirBundle<T>);
        } else {
          throw new Error('Invalid FHIR Bundle response');
        }
      } catch (err: unknown) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'An unexpected error occurred');
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void fetchBundle();

    return () => {
      cancelled = true;
    };
  }, [fhirBaseUrl, resourceType, patientUuid]);

  return { bundle, loading, error };
}
