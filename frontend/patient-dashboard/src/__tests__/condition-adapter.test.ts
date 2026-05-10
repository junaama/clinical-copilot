import type { FhirBundle, FhirCondition } from '../fhir-types';
import { adaptConditions, type ProblemItem } from '../adapters/condition-adapter';

function makeBundle(entries: FhirCondition[]): FhirBundle<FhirCondition> {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: entries.map((resource) => ({ resource })),
  };
}

const COMPLETE_CONDITION: FhirCondition = {
  resourceType: 'Condition',
  id: 'cond-001',
  clinicalStatus: { coding: [{ code: 'active' }] },
  verificationStatus: { coding: [{ code: 'confirmed' }] },
  category: [{ coding: [{ code: 'problem-list-item', display: 'Problem List Item' }] }],
  code: {
    coding: [{ display: 'Type 2 Diabetes Mellitus', code: 'E11' }],
    text: 'Type 2 Diabetes Mellitus',
  },
  onsetDateTime: '2020-03-10',
  recordedDate: '2020-03-15',
};

describe('adaptConditions', () => {
  it('maps a complete Condition to a ProblemItem', () => {
    const result = adaptConditions(makeBundle([COMPLETE_CONDITION]));

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual<ProblemItem>({
      id: 'cond-001',
      title: 'Type 2 Diabetes Mellitus',
      titleQualifier: null,
      clinicalStatus: 'active',
      onsetDate: '2020-03-10',
      recordedDate: '2020-03-15',
    });
  });

  it('handles missing optional fields', () => {
    const sparse: FhirCondition = {
      resourceType: 'Condition',
      id: 'cond-002',
      code: { coding: [{ display: 'Hypertension' }] },
    };
    const result = adaptConditions(makeBundle([sparse]));

    expect(result).toHaveLength(1);
    expect(result[0].title).toBe('Hypertension');
    expect(result[0].clinicalStatus).toBe('unknown');
    expect(result[0].onsetDate).toBeNull();
    expect(result[0].recordedDate).toBeNull();
  });

  it('returns empty array for empty bundle', () => {
    const bundle: FhirBundle<FhirCondition> = {
      resourceType: 'Bundle',
      type: 'searchset',
    };
    expect(adaptConditions(bundle)).toEqual([]);
  });

  it('handles resolved conditions', () => {
    const resolved: FhirCondition = {
      resourceType: 'Condition',
      clinicalStatus: { coding: [{ code: 'resolved' }] },
      code: { text: 'Seasonal Allergic Rhinitis' },
    };
    const [item] = adaptConditions(makeBundle([resolved]));
    expect(item.clinicalStatus).toBe('resolved');
  });

  it('prefers code.text over coding display', () => {
    const condition: FhirCondition = {
      resourceType: 'Condition',
      code: { text: 'Chronic kidney disease stage 3', coding: [{ display: 'CKD' }] },
    };
    const [item] = adaptConditions(makeBundle([condition]));
    expect(item.title).toBe('Chronic kidney disease stage 3');
  });

  it('splits a trailing semantic tag into a title qualifier', () => {
    const condition: FhirCondition = {
      resourceType: 'Condition',
      code: { text: 'Hypertension (finding)' },
    };
    const [item] = adaptConditions(makeBundle([condition]));

    expect(item.title).toBe('Hypertension');
    expect(item.titleQualifier).toBe('finding');
  });

  it('returns "Unknown" title when code is absent', () => {
    const condition: FhirCondition = { resourceType: 'Condition' };
    const [item] = adaptConditions(makeBundle([condition]));
    expect(item.title).toBe('Unknown');
  });

  it('maps multiple conditions', () => {
    const second: FhirCondition = {
      resourceType: 'Condition',
      id: 'cond-003',
      code: { text: 'Asthma' },
      clinicalStatus: { coding: [{ code: 'inactive' }] },
    };
    const result = adaptConditions(makeBundle([COMPLETE_CONDITION, second]));
    expect(result).toHaveLength(2);
    expect(result[1].title).toBe('Asthma');
    expect(result[1].clinicalStatus).toBe('inactive');
  });

  it('skips entries without a resource', () => {
    const bundle: FhirBundle<FhirCondition> = {
      resourceType: 'Bundle',
      type: 'searchset',
      entry: [
        { resource: COMPLETE_CONDITION },
        {} as { resource: FhirCondition },
      ],
    };
    expect(adaptConditions(bundle)).toHaveLength(1);
  });
});
