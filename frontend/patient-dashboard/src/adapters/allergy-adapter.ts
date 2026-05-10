/**
 * Maps FHIR R4 AllergyIntolerance resources into dashboard-friendly DTOs.
 */

import type { FhirAllergyIntolerance, FhirBundle, FhirCodeableConcept } from '../fhir-types';

export interface AllergyItem {
  readonly id: string;
  readonly title: string;
  readonly titleQualifier: string | null;
  readonly clinicalStatus: string;
  readonly category: string;
  readonly criticality: string;
  readonly recordedDate: string | null;
  readonly reaction: string | null;
}

interface DisplayParts {
  readonly title: string;
  readonly qualifier: string | null;
}

/** Extract display text from a CodeableConcept, preferring text over coding. */
export function extractCodeableDisplay(concept: FhirCodeableConcept | undefined): string {
  if (!concept) return 'Unknown';
  if (concept.text) return concept.text;
  const display = concept.coding?.[0]?.display;
  return display ?? 'Unknown';
}

/** Split SNOMED-style semantic tags like "Peanut (substance)" for UI display. */
export function splitTrailingQualifier(display: string): DisplayParts {
  const match = display.match(/^(.+?)\s+\(([^()]+)\)$/);
  if (!match) {
    return { title: display, qualifier: null };
  }

  return {
    title: match[1]?.trim() ?? display,
    qualifier: match[2]?.trim() ?? null,
  };
}

/** Extract the first coding code from a CodeableConcept. */
function extractCodingCode(concept: FhirCodeableConcept | undefined): string {
  return concept?.coding?.[0]?.code ?? 'unknown';
}

export function adaptAllergies(bundle: FhirBundle<FhirAllergyIntolerance>): AllergyItem[] {
  if (!bundle.entry) return [];

  return bundle.entry
    .filter((e): e is { resource: FhirAllergyIntolerance } => e.resource != null)
    .map((e) => adaptOneAllergy(e.resource));
}

function adaptOneAllergy(allergy: FhirAllergyIntolerance): AllergyItem {
  const display = splitTrailingQualifier(extractCodeableDisplay(allergy.code));

  return {
    id: allergy.id ?? '',
    title: display.title,
    titleQualifier: display.qualifier,
    clinicalStatus: extractCodingCode(allergy.clinicalStatus),
    category: allergy.category?.[0] ?? 'unknown',
    criticality: allergy.criticality ?? 'unknown',
    recordedDate: allergy.recordedDate ?? null,
    reaction: allergy.reaction?.[0]?.manifestation?.[0]?.text
      ?? allergy.reaction?.[0]?.manifestation?.[0]?.coding?.[0]?.display
      ?? null,
  };
}
