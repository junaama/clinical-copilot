/**
 * Maps FHIR R4 CareTeam resources into dashboard-friendly DTOs.
 */

import type { FhirBundle, FhirCareTeam, FhirCareTeamParticipant } from '../fhir-types';
import { extractCodeableDisplay } from './allergy-adapter';

export type CareTeamMemberType = 'practitioner' | 'organization' | 'related-person' | 'unknown';

export interface CareTeamMember {
  readonly name: string;
  readonly role: string;
  readonly memberType: CareTeamMemberType;
  readonly facility: string | null;
  readonly since: string | null;
}

export interface CareTeamData {
  readonly id: string;
  readonly name: string;
  readonly status: string;
  readonly members: readonly CareTeamMember[];
}

export function adaptCareTeams(bundle: FhirBundle<FhirCareTeam>): CareTeamData[] {
  if (!bundle.entry) return [];

  return bundle.entry
    .filter((e): e is { resource: FhirCareTeam } => e.resource != null)
    .map((e) => adaptOneCareTeam(e.resource));
}

function adaptOneCareTeam(team: FhirCareTeam): CareTeamData {
  return {
    id: team.id ?? '',
    name: team.name ?? 'Care Team',
    status: team.status ?? 'unknown',
    members: (team.participant ?? []).map(adaptOneParticipant),
  };
}

function adaptOneParticipant(participant: FhirCareTeamParticipant): CareTeamMember {
  const role = participant.role?.[0]
    ? extractCodeableDisplay(participant.role[0])
    : 'Unknown';

  const name = participant.member?.display ?? 'Unknown';
  const memberType = inferMemberType(participant.member?.reference);
  const facility = participant.onBehalfOf?.display ?? null;
  const since = participant.period?.start ?? null;

  return { name, role, memberType, facility, since };
}

function inferMemberType(reference: string | undefined): CareTeamMemberType {
  if (!reference) return 'unknown';
  if (reference.startsWith('Practitioner/')) return 'practitioner';
  if (reference.startsWith('Organization/')) return 'organization';
  if (reference.startsWith('RelatedPerson/')) return 'related-person';
  if (reference.startsWith('Patient/')) return 'practitioner';
  return 'unknown';
}
