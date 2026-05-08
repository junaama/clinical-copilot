/**
 * Minimal FHIR R4 type definitions used by the patient dashboard.
 * Only the fields the dashboard reads are modeled — not a complete FHIR spec.
 */

export interface FhirHumanName {
  readonly use?: string;
  readonly family?: string;
  readonly given?: readonly string[];
  readonly prefix?: readonly string[];
  readonly suffix?: readonly string[];
  readonly text?: string;
}

export interface FhirIdentifier {
  readonly use?: string;
  readonly type?: {
    readonly coding?: readonly { readonly code?: string }[];
  };
  readonly value?: string;
}

export interface FhirPatient {
  readonly resourceType: 'Patient';
  readonly id?: string;
  readonly active?: boolean;
  readonly name?: readonly FhirHumanName[];
  readonly birthDate?: string;
  readonly gender?: string;
  readonly identifier?: readonly FhirIdentifier[];
}
