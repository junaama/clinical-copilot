/** Boot configuration inlined by the PHP host route. */
export interface PatientDashboardConfig {
  /** Internal OpenEMR patient ID (numeric). */
  readonly pid: number;
  /** FHIR-compatible patient UUID string. */
  readonly patientUuid: string;
  /** OpenEMR web root path (e.g. "" or "/openemr"). */
  readonly webRoot: string;
  /** Base URL for the FHIR R4 API (e.g. "/apis/default/fhir/r4"). */
  readonly fhirBaseUrl: string;
  /** URL to the legacy demographics page. */
  readonly legacyDashboardUrl: string;
  /** URL to the modern demographics page (self). */
  readonly modernDashboardUrl: string;
  /** CSRF token for form submissions. */
  readonly csrfToken: string;
}

declare global {
  interface Window {
    __OPENEMR_PATIENT_DASHBOARD__?: PatientDashboardConfig;
  }
}
