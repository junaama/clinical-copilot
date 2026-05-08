/**
 * React hook for fetching a FHIR Patient resource from the OpenEMR API.
 */

import { useEffect, useState } from 'react';
import type { FhirPatient } from '../fhir-types';

export interface UseFhirPatientResult {
  readonly patient: FhirPatient | null;
  readonly loading: boolean;
  readonly error: string | null;
}

/**
 * Fetch a FHIR Patient resource by UUID.
 *
 * @param fhirBaseUrl - Base URL for the FHIR R4 API
 * @param patientUuid - FHIR-compatible patient UUID
 */
export function useFhirPatient(
  fhirBaseUrl: string,
  patientUuid: string,
): UseFhirPatientResult {
  const [patient, setPatient] = useState<FhirPatient | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchPatient(): Promise<void> {
      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          `${fhirBaseUrl}/Patient/${encodeURIComponent(patientUuid)}`,
          {
            credentials: 'same-origin',
            headers: { Accept: 'application/fhir+json' },
          },
        );

        if (!response.ok) {
          throw new Error(`Failed to load patient data (HTTP ${response.status})`);
        }

        const data: unknown = await response.json();

        if (cancelled) return;

        // Minimal runtime guard
        if (
          typeof data === 'object' &&
          data !== null &&
          'resourceType' in data &&
          (data as { resourceType: unknown }).resourceType === 'Patient'
        ) {
          setPatient(data as FhirPatient);
        } else {
          throw new Error('Invalid FHIR Patient response');
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

    void fetchPatient();

    return () => {
      cancelled = true;
    };
  }, [fhirBaseUrl, patientUuid]);

  return { patient, loading, error };
}
