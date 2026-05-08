/* ------------------------------------------------------------------ */
/*  CareTeam edit metadata (injected by PHP boot config)               */
/* ------------------------------------------------------------------ */

export interface CareTeamEditUser {
  readonly id: number;
  readonly name: string;
  readonly physicianType: string | null;
}

export interface CareTeamEditRelatedPerson {
  readonly id: number;
  readonly name: string;
  readonly relationship: string | null;
}

export interface CareTeamEditFacility {
  readonly id: number;
  readonly name: string;
}

export interface CareTeamEditRole {
  readonly id: string;
  readonly title: string;
}

export interface CareTeamEditStatus {
  readonly id: string;
  readonly title: string;
}

export interface CareTeamEditMember {
  readonly memberType: 'user' | 'contact';
  readonly userId: number | null;
  readonly contactId: number | null;
  readonly role: string;
  readonly facilityId: number | null;
  readonly providerSince: string | null;
  readonly status: string;
  readonly note: string | null;
  readonly userName: string | null;
  readonly contactName: string | null;
}

export interface CareTeamEditConfig {
  readonly teamId: number | null;
  readonly teamName: string;
  readonly teamStatus: string;
  readonly users: readonly CareTeamEditUser[];
  readonly relatedPersons: readonly CareTeamEditRelatedPerson[];
  readonly facilities: readonly CareTeamEditFacility[];
  readonly roles: readonly CareTeamEditRole[];
  readonly statuses: readonly CareTeamEditStatus[];
  readonly existingMembers: readonly CareTeamEditMember[];
}

/* ------------------------------------------------------------------ */
/*  Boot configuration                                                 */
/* ------------------------------------------------------------------ */

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
  /** CareTeam edit metadata (optional — absent if user lacks ACL). */
  readonly careTeamEdit?: CareTeamEditConfig;
}

declare global {
  interface Window {
    __OPENEMR_PATIENT_DASHBOARD__?: PatientDashboardConfig;
  }
}
