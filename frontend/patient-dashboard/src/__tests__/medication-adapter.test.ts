import type { FhirBundle, FhirMedicationRequest } from '../fhir-types';
import { adaptMedications, type MedicationItem } from '../adapters/medication-adapter';

function makeBundle(entries: FhirMedicationRequest[]): FhirBundle<FhirMedicationRequest> {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: entries.map((resource) => ({ resource })),
  };
}

const COMPLETE_MED_REQUEST: FhirMedicationRequest = {
  resourceType: 'MedicationRequest',
  id: 'med-001',
  status: 'active',
  intent: 'order',
  medicationCodeableConcept: {
    coding: [{ display: 'Metformin 500mg', code: '6809' }],
    text: 'Metformin 500mg tablet',
  },
  authoredOn: '2024-06-01',
  dosageInstruction: [{ text: '1 tablet twice daily with meals' }],
  requester: { display: 'Dr. Lopez', reference: 'Practitioner/pract-001' },
};

describe('adaptMedications', () => {
  it('maps a complete MedicationRequest to a MedicationItem', () => {
    const result = adaptMedications(makeBundle([COMPLETE_MED_REQUEST]));

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual<MedicationItem>({
      id: 'med-001',
      title: 'Metformin 500mg tablet',
      status: 'active',
      intent: 'order',
      authoredOn: '2024-06-01',
      dosage: '1 tablet twice daily with meals',
      requester: 'Dr. Lopez',
    });
  });

  it('handles missing optional fields', () => {
    const sparse: FhirMedicationRequest = {
      resourceType: 'MedicationRequest',
      id: 'med-002',
      medicationCodeableConcept: { coding: [{ display: 'Lisinopril 10mg' }] },
    };
    const result = adaptMedications(makeBundle([sparse]));

    expect(result).toHaveLength(1);
    expect(result[0].title).toBe('Lisinopril 10mg');
    expect(result[0].status).toBe('unknown');
    expect(result[0].intent).toBe('unknown');
    expect(result[0].authoredOn).toBeNull();
    expect(result[0].dosage).toBeNull();
    expect(result[0].requester).toBeNull();
  });

  it('prefers medicationCodeableConcept.text over coding display', () => {
    const med: FhirMedicationRequest = {
      resourceType: 'MedicationRequest',
      medicationCodeableConcept: {
        text: 'Atorvastatin 20mg oral tablet',
        coding: [{ display: 'Atorvastatin' }],
      },
    };
    const [item] = adaptMedications(makeBundle([med]));
    expect(item.title).toBe('Atorvastatin 20mg oral tablet');
  });

  it('falls back to medicationReference.display when concept is absent', () => {
    const med: FhirMedicationRequest = {
      resourceType: 'MedicationRequest',
      medicationReference: { display: 'Amlodipine 5mg', reference: 'Medication/med-ref-1' },
    };
    const [item] = adaptMedications(makeBundle([med]));
    expect(item.title).toBe('Amlodipine 5mg');
  });

  it('returns "Unknown" title when both medication fields are absent', () => {
    const med: FhirMedicationRequest = { resourceType: 'MedicationRequest' };
    const [item] = adaptMedications(makeBundle([med]));
    expect(item.title).toBe('Unknown');
  });

  it('returns empty array for empty bundle', () => {
    const bundle: FhirBundle<FhirMedicationRequest> = {
      resourceType: 'Bundle',
      type: 'searchset',
    };
    expect(adaptMedications(bundle)).toEqual([]);
  });

  it('maps multiple medication requests', () => {
    const second: FhirMedicationRequest = {
      resourceType: 'MedicationRequest',
      id: 'med-003',
      status: 'completed',
      intent: 'plan',
      medicationCodeableConcept: { text: 'Amoxicillin 250mg' },
    };
    const result = adaptMedications(makeBundle([COMPLETE_MED_REQUEST, second]));
    expect(result).toHaveLength(2);
    expect(result[1].title).toBe('Amoxicillin 250mg');
    expect(result[1].status).toBe('completed');
    expect(result[1].intent).toBe('plan');
  });

  it('skips entries without a resource', () => {
    const bundle: FhirBundle<FhirMedicationRequest> = {
      resourceType: 'Bundle',
      type: 'searchset',
      entry: [
        { resource: COMPLETE_MED_REQUEST },
        {} as { resource: FhirMedicationRequest },
      ],
    };
    expect(adaptMedications(bundle)).toHaveLength(1);
  });
});
