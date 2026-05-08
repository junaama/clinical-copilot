import type { FhirBundle, FhirCareTeam } from '../fhir-types';
import { adaptCareTeams, type CareTeamData, type CareTeamMember } from '../adapters/careteam-adapter';

function makeBundle(
  entries: FhirCareTeam[],
): FhirBundle<FhirCareTeam> {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: entries.map((resource) => ({ resource })),
  };
}

const COMPLETE_CARETEAM: FhirCareTeam = {
  resourceType: 'CareTeam',
  id: 'ct-1',
  status: 'active',
  name: 'Primary Care Team',
  subject: { reference: 'Patient/p-1', display: 'Test Patient' },
  participant: [
    {
      role: [{ coding: [{ code: '309343006', display: 'Physician' }] }],
      member: { reference: 'Practitioner/pr-1', display: 'Dr. Smith' },
      onBehalfOf: { reference: 'Organization/org-1', display: 'Main Clinic' },
      period: { start: '2023-01-15' },
    },
    {
      role: [{ text: 'Caregiver' }],
      member: { reference: 'RelatedPerson/rp-1', display: 'Jane Doe' },
    },
    {
      role: [{ coding: [{ display: 'Facility' }] }],
      member: { reference: 'Organization/org-2', display: 'Downtown Hospital' },
    },
  ],
  note: [{ text: 'Annual care plan review completed.' }],
};

describe('adaptCareTeams', () => {
  it('maps a complete CareTeam resource', () => {
    const result = adaptCareTeams(makeBundle([COMPLETE_CARETEAM]));

    expect(result).toHaveLength(1);
    const team: CareTeamData = result[0];
    expect(team.id).toBe('ct-1');
    expect(team.name).toBe('Primary Care Team');
    expect(team.status).toBe('active');
    expect(team.members).toHaveLength(3);
  });

  it('maps practitioner participant correctly', () => {
    const result = adaptCareTeams(makeBundle([COMPLETE_CARETEAM]));
    const member: CareTeamMember = result[0].members[0];

    expect(member.name).toBe('Dr. Smith');
    expect(member.role).toBe('Physician');
    expect(member.memberType).toBe('practitioner');
    expect(member.facility).toBe('Main Clinic');
    expect(member.since).toBe('2023-01-15');
  });

  it('maps related person participant correctly', () => {
    const result = adaptCareTeams(makeBundle([COMPLETE_CARETEAM]));
    const member: CareTeamMember = result[0].members[1];

    expect(member.name).toBe('Jane Doe');
    expect(member.role).toBe('Caregiver');
    expect(member.memberType).toBe('related-person');
    expect(member.facility).toBeNull();
    expect(member.since).toBeNull();
  });

  it('maps organization participant correctly', () => {
    const result = adaptCareTeams(makeBundle([COMPLETE_CARETEAM]));
    const member: CareTeamMember = result[0].members[2];

    expect(member.name).toBe('Downtown Hospital');
    expect(member.role).toBe('Facility');
    expect(member.memberType).toBe('organization');
  });

  it('returns empty array for empty bundle', () => {
    const emptyBundle: FhirBundle<FhirCareTeam> = {
      resourceType: 'Bundle',
      type: 'searchset',
    };
    expect(adaptCareTeams(emptyBundle)).toEqual([]);
  });

  it('returns empty array for bundle with no entries', () => {
    const emptyBundle: FhirBundle<FhirCareTeam> = {
      resourceType: 'Bundle',
      type: 'searchset',
      entry: [],
    };
    expect(adaptCareTeams(emptyBundle)).toEqual([]);
  });

  it('handles CareTeam with no participants', () => {
    const noParticipants: FhirCareTeam = {
      resourceType: 'CareTeam',
      id: 'ct-empty',
      status: 'active',
      name: 'Empty Team',
    };
    const result = adaptCareTeams(makeBundle([noParticipants]));

    expect(result).toHaveLength(1);
    expect(result[0].members).toEqual([]);
    expect(result[0].name).toBe('Empty Team');
  });

  it('handles missing optional fields with safe defaults', () => {
    const sparse: FhirCareTeam = {
      resourceType: 'CareTeam',
    };
    const result = adaptCareTeams(makeBundle([sparse]));

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('');
    expect(result[0].name).toBe('Care Team');
    expect(result[0].status).toBe('unknown');
    expect(result[0].members).toEqual([]);
  });

  it('handles participant with missing member reference', () => {
    const teamWithSparse: FhirCareTeam = {
      resourceType: 'CareTeam',
      id: 'ct-sparse',
      participant: [
        { role: [{ text: 'Nurse' }] },
      ],
    };
    const result = adaptCareTeams(makeBundle([teamWithSparse]));
    const member = result[0].members[0];

    expect(member.name).toBe('Unknown');
    expect(member.role).toBe('Nurse');
    expect(member.memberType).toBe('unknown');
  });

  it('handles participant with no role', () => {
    const teamNoRole: FhirCareTeam = {
      resourceType: 'CareTeam',
      id: 'ct-norole',
      participant: [
        { member: { reference: 'Practitioner/pr-2', display: 'Dr. Jones' } },
      ],
    };
    const result = adaptCareTeams(makeBundle([teamNoRole]));
    const member = result[0].members[0];

    expect(member.name).toBe('Dr. Jones');
    expect(member.role).toBe('Unknown');
  });

  it('maps multiple CareTeam resources', () => {
    const secondTeam: FhirCareTeam = {
      resourceType: 'CareTeam',
      id: 'ct-2',
      status: 'inactive',
      name: 'Cardiology Team',
    };
    const result = adaptCareTeams(makeBundle([COMPLETE_CARETEAM, secondTeam]));

    expect(result).toHaveLength(2);
    expect(result[0].name).toBe('Primary Care Team');
    expect(result[1].name).toBe('Cardiology Team');
    expect(result[1].status).toBe('inactive');
  });

  it('detects member type from reference string', () => {
    const team: FhirCareTeam = {
      resourceType: 'CareTeam',
      id: 'ct-types',
      participant: [
        { member: { reference: 'Practitioner/pr-1', display: 'A' } },
        { member: { reference: 'Organization/org-1', display: 'B' } },
        { member: { reference: 'RelatedPerson/rp-1', display: 'C' } },
        { member: { reference: 'Patient/pt-1', display: 'D' } },
        { member: { display: 'E' } },
      ],
    };
    const result = adaptCareTeams(makeBundle([team]));
    const types = result[0].members.map((m) => m.memberType);

    expect(types).toEqual([
      'practitioner',
      'organization',
      'related-person',
      'practitioner', // Patient mapped to practitioner-like
      'unknown',
    ]);
  });

  it('skips entries with null resource', () => {
    const bundle: FhirBundle<FhirCareTeam> = {
      resourceType: 'Bundle',
      type: 'searchset',
      entry: [
        { resource: COMPLETE_CARETEAM },
        { resource: undefined as unknown as FhirCareTeam },
      ],
    };
    const result = adaptCareTeams(bundle);
    expect(result).toHaveLength(1);
  });
});
