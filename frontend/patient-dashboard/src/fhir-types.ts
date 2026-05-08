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

/* ------------------------------------------------------------------ */
/*  Shared FHIR building blocks                                       */
/* ------------------------------------------------------------------ */

export interface FhirCodeableConcept {
  readonly coding?: readonly { readonly code?: string; readonly display?: string; readonly system?: string }[];
  readonly text?: string;
}

export interface FhirReference {
  readonly reference?: string;
  readonly display?: string;
}

export interface FhirPeriod {
  readonly start?: string;
  readonly end?: string;
}

/** Generic FHIR Bundle wrapper for search results. */
export interface FhirBundle<T> {
  readonly resourceType: 'Bundle';
  readonly type: string;
  readonly total?: number;
  readonly entry?: readonly { readonly resource?: T }[];
}

/* ------------------------------------------------------------------ */
/*  AllergyIntolerance                                                */
/* ------------------------------------------------------------------ */

export interface FhirAllergyIntolerance {
  readonly resourceType: 'AllergyIntolerance';
  readonly id?: string;
  readonly clinicalStatus?: FhirCodeableConcept;
  readonly verificationStatus?: FhirCodeableConcept;
  readonly type?: string;
  readonly category?: readonly string[];
  readonly criticality?: string;
  readonly code?: FhirCodeableConcept;
  readonly recordedDate?: string;
  readonly reaction?: readonly {
    readonly manifestation: readonly FhirCodeableConcept[];
  }[];
}

/* ------------------------------------------------------------------ */
/*  Condition (Problem List)                                          */
/* ------------------------------------------------------------------ */

export interface FhirCondition {
  readonly resourceType: 'Condition';
  readonly id?: string;
  readonly clinicalStatus?: FhirCodeableConcept;
  readonly verificationStatus?: FhirCodeableConcept;
  readonly category?: readonly FhirCodeableConcept[];
  readonly code?: FhirCodeableConcept;
  readonly onsetDateTime?: string;
  readonly recordedDate?: string;
}

/* ------------------------------------------------------------------ */
/*  MedicationRequest                                                 */
/* ------------------------------------------------------------------ */

export interface FhirDosageInstruction {
  readonly text?: string;
}

export interface FhirMedicationRequest {
  readonly resourceType: 'MedicationRequest';
  readonly id?: string;
  readonly status?: string;
  readonly intent?: string;
  readonly medicationCodeableConcept?: FhirCodeableConcept;
  readonly medicationReference?: FhirReference;
  readonly authoredOn?: string;
  readonly dosageInstruction?: readonly FhirDosageInstruction[];
  readonly requester?: FhirReference;
}

/* ------------------------------------------------------------------ */
/*  Encounter                                                          */
/* ------------------------------------------------------------------ */

export interface FhirEncounter {
  readonly resourceType: 'Encounter';
  readonly id?: string;
  readonly status?: string;
  readonly class?: FhirCodeableConcept;
  readonly type?: readonly FhirCodeableConcept[];
  readonly period?: FhirPeriod;
  readonly reasonCode?: readonly FhirCodeableConcept[];
  readonly identifier?: readonly FhirIdentifier[];
}

/* ------------------------------------------------------------------ */
/*  CareTeam                                                           */
/* ------------------------------------------------------------------ */

export interface FhirCareTeamParticipant {
  readonly role?: readonly FhirCodeableConcept[];
  readonly member?: FhirReference;
  readonly onBehalfOf?: FhirReference;
  readonly period?: FhirPeriod;
}

export interface FhirAnnotation {
  readonly text?: string;
}

export interface FhirCareTeam {
  readonly resourceType: 'CareTeam';
  readonly id?: string;
  readonly status?: string;
  readonly name?: string;
  readonly subject?: FhirReference;
  readonly participant?: readonly FhirCareTeamParticipant[];
  readonly note?: readonly FhirAnnotation[];
}
