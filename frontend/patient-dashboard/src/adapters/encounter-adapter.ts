/**
 * Maps FHIR R4 Encounter resources into dashboard-friendly Encounter History DTOs.
 */

import type { FhirEncounter, FhirBundle } from '../fhir-types';
import { extractCodeableDisplay } from './allergy-adapter';

export interface EncounterItem {
  readonly id: string;
  readonly status: string;
  readonly type: string;
  readonly reason: string | null;
  readonly startDate: string | null;
  readonly endDate: string | null;
}

export function adaptEncounters(bundle: FhirBundle<FhirEncounter>): EncounterItem[] {
  if (!bundle.entry) return [];

  return bundle.entry
    .filter((e): e is { resource: FhirEncounter } => e.resource != null)
    .map((e) => adaptOneEncounter(e.resource));
}

function adaptOneEncounter(encounter: FhirEncounter): EncounterItem {
  const firstType = encounter.type?.[0];
  const firstReason = encounter.reasonCode?.[0];

  return {
    id: encounter.id ?? '',
    status: encounter.status ?? 'unknown',
    type: extractCodeableDisplay(firstType),
    reason: firstReason ? extractCodeableDisplay(firstReason) : null,
    startDate: encounter.period?.start ?? null,
    endDate: encounter.period?.end ?? null,
  };
}
