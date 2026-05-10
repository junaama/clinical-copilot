/**
 * Maps a FHIR R4 Patient resource to a PatientHeaderData DTO for the dashboard header.
 */

import type { FhirPatient } from '../fhir-types';
import { cleanSyntheticNameSuffixes } from '../display-name';

/** UI DTO for the patient identity header. */
export interface PatientHeaderData {
  readonly fullName: string;
  readonly dateOfBirth: string;
  readonly sex: string;
  readonly mrn: string;
  readonly active: boolean;
}

/** Sentinel returned when patient data is unavailable or empty. */
export const EMPTY_PATIENT_HEADER: PatientHeaderData = {
  fullName: 'Unknown Patient',
  dateOfBirth: 'Unknown',
  sex: 'Unknown',
  mrn: 'Unknown',
  active: false,
};

/**
 * Adapt a FHIR Patient resource into a PatientHeaderData DTO.
 * Handles partial and missing fields gracefully.
 */
export function adaptPatient(patient: FhirPatient): PatientHeaderData {
  return {
    fullName: extractFullName(patient),
    dateOfBirth: patient.birthDate ?? 'Unknown',
    sex: formatGender(patient.gender),
    mrn: extractMrn(patient),
    active: patient.active ?? false,
  };
}

/** Extract the best available display name from a FHIR Patient. */
function extractFullName(patient: FhirPatient): string {
  if (!patient.name || patient.name.length === 0) {
    return 'Unknown Patient';
  }

  // Prefer the "official" name, fall back to the first entry
  const official = patient.name.find((n) => n.use === 'official');
  const name = official ?? patient.name[0];

  // If text is provided, use it directly
  if (name.text) {
    return cleanSyntheticNameSuffixes(name.text);
  }

  const parts: string[] = [];
  if (name.prefix && name.prefix.length > 0) {
    parts.push(name.prefix.join(' '));
  }
  if (name.given && name.given.length > 0) {
    parts.push(name.given.join(' '));
  }
  if (name.family) {
    parts.push(name.family);
  }
  if (name.suffix && name.suffix.length > 0) {
    parts.push(name.suffix.join(' '));
  }

  return parts.length > 0 ? cleanSyntheticNameSuffixes(parts.join(' ')) : 'Unknown Patient';
}

/** Format FHIR gender code into display text. */
function formatGender(gender: string | undefined): string {
  switch (gender) {
    case 'male':
      return 'Male';
    case 'female':
      return 'Female';
    case 'other':
      return 'Other';
    case 'unknown':
      return 'Unknown';
    default:
      return 'Unknown';
  }
}

/** Extract the MRN (SS type identifier) from FHIR identifiers. */
function extractMrn(patient: FhirPatient): string {
  if (!patient.identifier || patient.identifier.length === 0) {
    return 'Unknown';
  }

  // OpenEMR uses type.coding[0].code = "SS" for the pubpid/MRN
  const mrnIdentifier = patient.identifier.find((id) =>
    id.type?.coding?.some((c) => c.code === 'SS'),
  );

  if (mrnIdentifier?.value) {
    return mrnIdentifier.value;
  }

  // Fallback: return the first identifier with a value
  const firstWithValue = patient.identifier.find((id) => id.value);
  return firstWithValue?.value ?? 'Unknown';
}
