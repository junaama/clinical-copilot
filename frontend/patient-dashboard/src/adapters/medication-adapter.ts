/**
 * Maps FHIR R4 MedicationRequest resources into dashboard-friendly DTOs.
 * Covers both Medications and Prescriptions cards (distinguished by intent).
 */

import type { FhirMedicationRequest, FhirBundle } from '../fhir-types';
import { extractCodeableDisplay } from './allergy-adapter';

export interface MedicationItem {
  readonly id: string;
  readonly title: string;
  readonly status: string;
  readonly intent: string;
  readonly authoredOn: string | null;
  readonly dosage: string | null;
  readonly requester: string | null;
}

export function adaptMedications(bundle: FhirBundle<FhirMedicationRequest>): MedicationItem[] {
  if (!bundle.entry) return [];

  return bundle.entry
    .filter((e): e is { resource: FhirMedicationRequest } => e.resource != null)
    .map((e) => adaptOneMedication(e.resource));
}

function extractMedicationTitle(med: FhirMedicationRequest): string {
  // Prefer CodeableConcept (inline medication name)
  if (med.medicationCodeableConcept) {
    return extractCodeableDisplay(med.medicationCodeableConcept);
  }
  // Fall back to reference display
  if (med.medicationReference?.display) {
    return med.medicationReference.display;
  }
  return 'Unknown';
}

function adaptOneMedication(med: FhirMedicationRequest): MedicationItem {
  return {
    id: med.id ?? '',
    title: extractMedicationTitle(med),
    status: med.status ?? 'unknown',
    intent: med.intent ?? 'unknown',
    authoredOn: med.authoredOn ?? null,
    dosage: med.dosageInstruction?.[0]?.text ?? null,
    requester: med.requester?.display ?? null,
  };
}
