import type { FhirBundle, FhirAllergyIntolerance } from '../fhir-types';
import { adaptAllergies, type AllergyItem } from '../adapters/allergy-adapter';

function makeBundle(entries: FhirAllergyIntolerance[]): FhirBundle<FhirAllergyIntolerance> {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: entries.map((resource) => ({ resource })),
  };
}

const COMPLETE_ALLERGY: FhirAllergyIntolerance = {
  resourceType: 'AllergyIntolerance',
  id: 'allergy-001',
  clinicalStatus: {
    coding: [{ code: 'active', system: 'http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical' }],
  },
  verificationStatus: {
    coding: [{ code: 'confirmed' }],
  },
  type: 'allergy',
  category: ['medication'],
  criticality: 'high',
  code: {
    coding: [{ display: 'Penicillin', code: '7980' }],
    text: 'Penicillin',
  },
  recordedDate: '2024-01-15',
  reaction: [
    {
      manifestation: [{ coding: [{ display: 'Hives' }], text: 'Hives' }],
    },
  ],
};

describe('adaptAllergies', () => {
  it('maps a complete AllergyIntolerance to an AllergyItem', () => {
    const result = adaptAllergies(makeBundle([COMPLETE_ALLERGY]));

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual<AllergyItem>({
      id: 'allergy-001',
      title: 'Penicillin',
      clinicalStatus: 'active',
      category: 'medication',
      criticality: 'high',
      recordedDate: '2024-01-15',
      reaction: 'Hives',
    });
  });

  it('handles missing optional fields gracefully', () => {
    const sparse: FhirAllergyIntolerance = {
      resourceType: 'AllergyIntolerance',
      id: 'allergy-002',
      code: { coding: [{ display: 'Peanuts' }] },
    };
    const result = adaptAllergies(makeBundle([sparse]));

    expect(result).toHaveLength(1);
    expect(result[0].title).toBe('Peanuts');
    expect(result[0].clinicalStatus).toBe('unknown');
    expect(result[0].category).toBe('unknown');
    expect(result[0].criticality).toBe('unknown');
    expect(result[0].recordedDate).toBeNull();
    expect(result[0].reaction).toBeNull();
  });

  it('prefers code.text over coding display', () => {
    const allergy: FhirAllergyIntolerance = {
      resourceType: 'AllergyIntolerance',
      code: {
        text: 'Sulfonamide antibiotics',
        coding: [{ display: 'Sulfonamide' }],
      },
    };
    const [item] = adaptAllergies(makeBundle([allergy]));
    expect(item.title).toBe('Sulfonamide antibiotics');
  });

  it('falls back to coding display when text is missing', () => {
    const allergy: FhirAllergyIntolerance = {
      resourceType: 'AllergyIntolerance',
      code: { coding: [{ display: 'Latex' }] },
    };
    const [item] = adaptAllergies(makeBundle([allergy]));
    expect(item.title).toBe('Latex');
  });

  it('returns "Unknown" title when code is absent', () => {
    const allergy: FhirAllergyIntolerance = {
      resourceType: 'AllergyIntolerance',
    };
    const [item] = adaptAllergies(makeBundle([allergy]));
    expect(item.title).toBe('Unknown');
  });

  it('returns empty array for empty bundle', () => {
    const emptyBundle: FhirBundle<FhirAllergyIntolerance> = {
      resourceType: 'Bundle',
      type: 'searchset',
    };
    expect(adaptAllergies(emptyBundle)).toEqual([]);
  });

  it('maps multiple allergies', () => {
    const second: FhirAllergyIntolerance = {
      resourceType: 'AllergyIntolerance',
      id: 'allergy-003',
      code: { text: 'Dust mites' },
      category: ['environment'],
      clinicalStatus: { coding: [{ code: 'inactive' }] },
    };
    const result = adaptAllergies(makeBundle([COMPLETE_ALLERGY, second]));

    expect(result).toHaveLength(2);
    expect(result[0].title).toBe('Penicillin');
    expect(result[1].title).toBe('Dust mites');
    expect(result[1].clinicalStatus).toBe('inactive');
    expect(result[1].category).toBe('environment');
  });

  it('extracts reaction from first manifestation', () => {
    const allergy: FhirAllergyIntolerance = {
      resourceType: 'AllergyIntolerance',
      code: { text: 'Amoxicillin' },
      reaction: [
        {
          manifestation: [
            { text: 'Rash' },
            { text: 'Swelling' },
          ],
        },
      ],
    };
    const [item] = adaptAllergies(makeBundle([allergy]));
    expect(item.reaction).toBe('Rash');
  });

  it('skips bundle entries without a resource', () => {
    const bundle: FhirBundle<FhirAllergyIntolerance> = {
      resourceType: 'Bundle',
      type: 'searchset',
      entry: [
        { resource: COMPLETE_ALLERGY },
        {} as { resource: FhirAllergyIntolerance },
      ],
    };
    expect(adaptAllergies(bundle)).toHaveLength(1);
  });
});
