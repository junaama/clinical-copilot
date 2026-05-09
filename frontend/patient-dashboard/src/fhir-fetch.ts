/**
 * Shared fetch helpers for OpenEMR FHIR calls.
 */

function getApiCsrfToken(): string | null {
  return window.__OPENEMR_PATIENT_DASHBOARD__?.apiCsrfToken ?? null;
}

export function fhirRequestInit(): RequestInit {
  const headers: Record<string, string> = {
    Accept: 'application/fhir+json',
  };

  const apiCsrfToken = getApiCsrfToken();
  if (apiCsrfToken) {
    headers.APICSRFTOKEN = apiCsrfToken;
  }

  return {
    credentials: 'same-origin',
    headers,
  };
}
