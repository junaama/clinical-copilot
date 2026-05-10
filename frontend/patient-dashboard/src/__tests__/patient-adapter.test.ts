import type { FhirPatient } from '../fhir-types';
import { adaptPatient, EMPTY_PATIENT_HEADER } from '../adapters/patient-adapter';

/** A complete FHIR Patient fixture matching typical OpenEMR output. */
const COMPLETE_PATIENT: FhirPatient = {
  resourceType: 'Patient',
  id: '90cfdaa2-60ea-4b20-a6d9-1cf01aaaaabb',
  active: true,
  name: [
    {
      use: 'official',
      family: 'Chen',
      given: ['Eduardo', 'Miguel'],
      prefix: ['Mr.'],
    },
  ],
  birthDate: '1965-04-23',
  gender: 'male',
  identifier: [
    {
      use: 'official',
      type: { coding: [{ code: 'SS' }] },
      value: 'MRN-10042',
    },
  ],
};

describe('adaptPatient', () => {
  describe('complete Patient resource', () => {
    it('maps all identity fields correctly', () => {
      const result = adaptPatient(COMPLETE_PATIENT);

      expect(result.fullName).toBe('Mr. Eduardo Miguel Chen');
      expect(result.dateOfBirth).toBe('1965-04-23');
      expect(result.sex).toBe('Male');
      expect(result.mrn).toBe('MRN-10042');
      expect(result.active).toBe(true);
    });
  });

  describe('partial Patient resource', () => {
    it('handles missing given name', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ use: 'official', family: 'Chen' }],
        birthDate: '1965-04-23',
        gender: 'female',
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Chen');
      expect(result.sex).toBe('Female');
    });

    it('handles missing family name', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ given: ['Eduardo'] }],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Eduardo');
    });

    it('prefers official name over other uses', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [
          { use: 'nickname', given: ['Eddie'] },
          { use: 'official', family: 'Chen', given: ['Eduardo'] },
        ],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Eduardo Chen');
    });

    it('uses text display when provided', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ text: 'Dr. Eduardo Chen III' }],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Dr. Eduardo Chen III');
    });

    it('cleans synthetic numeric suffixes from display names', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ family: 'Covarrubias273', given: ['Patricia625', 'Raquel318'] }],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Patricia Raquel Covarrubias');
    });

    it('falls back to first name when no official use', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ family: 'Whitaker', given: ['Sandra'] }],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Sandra Whitaker');
    });

    it('handles missing birthDate', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ family: 'Chen' }],
      };
      const result = adaptPatient(patient);

      expect(result.dateOfBirth).toBe('Unknown');
    });

    it('handles missing gender', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ family: 'Chen' }],
      };
      const result = adaptPatient(patient);

      expect(result.sex).toBe('Unknown');
    });

    it('maps gender values correctly', () => {
      const genderCases: Array<{ input: string; expected: string }> = [
        { input: 'male', expected: 'Male' },
        { input: 'female', expected: 'Female' },
        { input: 'other', expected: 'Other' },
        { input: 'unknown', expected: 'Unknown' },
      ];

      for (const { input, expected } of genderCases) {
        const patient: FhirPatient = {
          resourceType: 'Patient',
          gender: input,
        };
        expect(adaptPatient(patient).sex).toBe(expected);
      }
    });

    it('handles identifier without SS type by falling back to first value', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        identifier: [
          { use: 'usual', value: 'FALLBACK-001' },
        ],
      };
      const result = adaptPatient(patient);

      expect(result.mrn).toBe('FALLBACK-001');
    });

    it('handles active=false', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        active: false,
        name: [{ family: 'Chen' }],
      };
      const result = adaptPatient(patient);

      expect(result.active).toBe(false);
    });

    it('handles name with suffix', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ family: 'Chen', given: ['Eduardo'], suffix: ['Jr.'] }],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Eduardo Chen Jr.');
    });
  });

  describe('missing Patient fields', () => {
    it('handles completely empty Patient resource', () => {
      const patient: FhirPatient = { resourceType: 'Patient' };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Unknown Patient');
      expect(result.dateOfBirth).toBe('Unknown');
      expect(result.sex).toBe('Unknown');
      expect(result.mrn).toBe('Unknown');
      expect(result.active).toBe(false);
    });

    it('handles empty name array', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Unknown Patient');
    });

    it('handles empty identifier array', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        identifier: [],
      };
      const result = adaptPatient(patient);

      expect(result.mrn).toBe('Unknown');
    });

    it('handles identifier with SS type but no value', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        identifier: [
          { type: { coding: [{ code: 'SS' }] } },
        ],
      };
      const result = adaptPatient(patient);

      expect(result.mrn).toBe('Unknown');
    });

    it('handles name with all empty arrays', () => {
      const patient: FhirPatient = {
        resourceType: 'Patient',
        name: [{ given: [], prefix: [], suffix: [] }],
      };
      const result = adaptPatient(patient);

      expect(result.fullName).toBe('Unknown Patient');
    });
  });

  describe('EMPTY_PATIENT_HEADER sentinel', () => {
    it('has expected default values', () => {
      expect(EMPTY_PATIENT_HEADER).toEqual({
        fullName: 'Unknown Patient',
        dateOfBirth: 'Unknown',
        sex: 'Unknown',
        mrn: 'Unknown',
        active: false,
      });
    });
  });
});
