/**
 * Persistent patient identity header for the modern dashboard.
 * Fetches the FHIR Patient resource and renders name, DOB, sex, MRN, and active status.
 */

import { useFhirPatient } from '../hooks/use-fhir-patient';
import { adaptPatient, EMPTY_PATIENT_HEADER } from '../adapters/patient-adapter';
import type { PatientHeaderData } from '../adapters/patient-adapter';

interface PatientHeaderProps {
  readonly fhirBaseUrl: string;
  readonly patientUuid: string;
}

export default function PatientHeader({ fhirBaseUrl, patientUuid }: PatientHeaderProps) {
  const { patient, loading, error } = useFhirPatient(fhirBaseUrl, patientUuid);

  if (loading) {
    return (
      <div className="patient-header patient-header--loading" data-testid="patient-header-loading" role="status">
        <span className="patient-header__spinner" aria-hidden="true" />
        <span>Loading patient information...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="patient-header patient-header--error" data-testid="patient-header-error" role="alert">
        <span className="patient-header__error-icon" aria-hidden="true" />
        <span>Unable to load patient information: {error}</span>
      </div>
    );
  }

  const data: PatientHeaderData = patient ? adaptPatient(patient) : EMPTY_PATIENT_HEADER;

  return (
    <div className="patient-header" data-testid="patient-header">
      <div className="patient-header__identity">
        <h2 className="patient-header__name" data-testid="patient-name">
          {data.fullName}
        </h2>
        <span
          className={`patient-header__status ${data.active ? 'patient-header__status--active' : 'patient-header__status--inactive'}`}
          data-testid="patient-status"
        >
          {data.active ? 'Active' : 'Inactive'}
        </span>
      </div>
      <dl className="patient-header__details">
        <div className="patient-header__detail">
          <dt>DOB</dt>
          <dd data-testid="patient-dob">{data.dateOfBirth}</dd>
        </div>
        <div className="patient-header__detail">
          <dt>Sex</dt>
          <dd data-testid="patient-sex">{data.sex}</dd>
        </div>
        <div className="patient-header__detail">
          <dt>MRN</dt>
          <dd data-testid="patient-mrn">{data.mrn}</dd>
        </div>
      </dl>
    </div>
  );
}
