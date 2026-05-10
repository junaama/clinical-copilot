/**
 * Maps FHIR R4 Condition resources into dashboard-friendly Problem List DTOs.
 */

import type { FhirCondition, FhirBundle } from '../fhir-types';
import { extractCodeableDisplay, splitTrailingQualifier } from './allergy-adapter';

export interface ProblemItem {
  readonly id: string;
  readonly title: string;
  readonly titleQualifier: string | null;
  readonly clinicalStatus: string;
  readonly onsetDate: string | null;
  readonly recordedDate: string | null;
}

export function adaptConditions(bundle: FhirBundle<FhirCondition>): ProblemItem[] {
  if (!bundle.entry) return [];

  return bundle.entry
    .filter((e): e is { resource: FhirCondition } => e.resource != null)
    .map((e) => adaptOneCondition(e.resource));
}

function adaptOneCondition(condition: FhirCondition): ProblemItem {
  const display = splitTrailingQualifier(extractCodeableDisplay(condition.code));

  return {
    id: condition.id ?? '',
    title: display.title,
    titleQualifier: display.qualifier,
    clinicalStatus: condition.clinicalStatus?.coding?.[0]?.code ?? 'unknown',
    onsetDate: condition.onsetDateTime ?? null,
    recordedDate: condition.recordedDate ?? null,
  };
}
