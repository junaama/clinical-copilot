import type { FhirBundle, FhirEncounter } from '../fhir-types';
import { adaptEncounters, type EncounterItem } from '../adapters/encounter-adapter';

function makeBundle(entries: FhirEncounter[]): FhirBundle<FhirEncounter> {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: entries.map((resource) => ({ resource })),
  };
}

const COMPLETE_ENCOUNTER: FhirEncounter = {
  resourceType: 'Encounter',
  id: 'enc-001',
  status: 'finished',
  class: {
    coding: [{ code: 'AMB', display: 'ambulatory' }],
  },
  type: [
    {
      coding: [{ display: 'Office Visit', code: '99213' }],
      text: 'Established Patient Office Visit',
    },
  ],
  period: {
    start: '2024-03-15T09:00:00Z',
    end: '2024-03-15T09:30:00Z',
  },
  reasonCode: [
    {
      coding: [{ display: 'Hypertension follow-up' }],
      text: 'Hypertension follow-up',
    },
  ],
};

describe('adaptEncounters', () => {
  it('maps a complete Encounter to an EncounterItem', () => {
    const result = adaptEncounters(makeBundle([COMPLETE_ENCOUNTER]));

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual<EncounterItem>({
      id: 'enc-001',
      status: 'finished',
      type: 'Established Patient Office Visit',
      reason: 'Hypertension follow-up',
      startDate: '2024-03-15T09:00:00Z',
      endDate: '2024-03-15T09:30:00Z',
    });
  });

  it('handles encounter with only period.start (no end)', () => {
    const enc: FhirEncounter = {
      resourceType: 'Encounter',
      id: 'enc-002',
      status: 'in-progress',
      period: {
        start: '2024-06-01T10:00:00Z',
      },
    };
    const [item] = adaptEncounters(makeBundle([enc]));

    expect(item.startDate).toBe('2024-06-01T10:00:00Z');
    expect(item.endDate).toBeNull();
    expect(item.status).toBe('in-progress');
  });

  it('handles encounter with no period at all', () => {
    const enc: FhirEncounter = {
      resourceType: 'Encounter',
      id: 'enc-003',
      status: 'planned',
      type: [{ coding: [{ display: 'Consultation' }] }],
    };
    const [item] = adaptEncounters(makeBundle([enc]));

    expect(item.startDate).toBeNull();
    expect(item.endDate).toBeNull();
    expect(item.type).toBe('Consultation');
  });

  it('returns empty array for empty bundle', () => {
    const emptyBundle: FhirBundle<FhirEncounter> = {
      resourceType: 'Bundle',
      type: 'searchset',
    };
    expect(adaptEncounters(emptyBundle)).toEqual([]);
  });

  it('returns empty array for bundle with no entries', () => {
    const emptyBundle: FhirBundle<FhirEncounter> = {
      resourceType: 'Bundle',
      type: 'searchset',
      entry: [],
    };
    expect(adaptEncounters(emptyBundle)).toEqual([]);
  });

  it('handles missing optional fields gracefully', () => {
    const sparse: FhirEncounter = {
      resourceType: 'Encounter',
      id: 'enc-004',
    };
    const [item] = adaptEncounters(makeBundle([sparse]));

    expect(item.id).toBe('enc-004');
    expect(item.status).toBe('unknown');
    expect(item.type).toBe('Unknown');
    expect(item.reason).toBeNull();
    expect(item.startDate).toBeNull();
    expect(item.endDate).toBeNull();
  });

  it('prefers type[0].text over type[0].coding[0].display', () => {
    const enc: FhirEncounter = {
      resourceType: 'Encounter',
      type: [
        {
          text: 'Annual Physical Exam',
          coding: [{ display: 'Office Visit' }],
        },
      ],
    };
    const [item] = adaptEncounters(makeBundle([enc]));
    expect(item.type).toBe('Annual Physical Exam');
  });

  it('falls back to type coding display when text is absent', () => {
    const enc: FhirEncounter = {
      resourceType: 'Encounter',
      type: [{ coding: [{ display: 'Emergency Visit' }] }],
    };
    const [item] = adaptEncounters(makeBundle([enc]));
    expect(item.type).toBe('Emergency Visit');
  });

  it('prefers reasonCode[0].text over coding display', () => {
    const enc: FhirEncounter = {
      resourceType: 'Encounter',
      reasonCode: [
        {
          text: 'Diabetes management',
          coding: [{ display: 'DM' }],
        },
      ],
    };
    const [item] = adaptEncounters(makeBundle([enc]));
    expect(item.reason).toBe('Diabetes management');
  });

  it('maps multiple encounters preserving order', () => {
    const second: FhirEncounter = {
      resourceType: 'Encounter',
      id: 'enc-005',
      status: 'cancelled',
      type: [{ text: 'Follow-up' }],
    };
    const result = adaptEncounters(makeBundle([COMPLETE_ENCOUNTER, second]));

    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('enc-001');
    expect(result[1].id).toBe('enc-005');
    expect(result[1].status).toBe('cancelled');
    expect(result[1].type).toBe('Follow-up');
  });

  it('skips bundle entries without a resource', () => {
    const bundle: FhirBundle<FhirEncounter> = {
      resourceType: 'Bundle',
      type: 'searchset',
      entry: [
        { resource: COMPLETE_ENCOUNTER },
        {} as { resource: FhirEncounter },
      ],
    };
    expect(adaptEncounters(bundle)).toHaveLength(1);
  });

  it('returns null reason when reasonCode is absent', () => {
    const enc: FhirEncounter = {
      resourceType: 'Encounter',
      type: [{ text: 'Check-up' }],
    };
    const [item] = adaptEncounters(makeBundle([enc]));
    expect(item.reason).toBeNull();
  });

  it('returns null reason when reasonCode is empty array', () => {
    const enc: FhirEncounter = {
      resourceType: 'Encounter',
      reasonCode: [],
    };
    const [item] = adaptEncounters(makeBundle([enc]));
    expect(item.reason).toBeNull();
  });
});
