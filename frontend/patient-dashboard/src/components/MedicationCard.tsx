/**
 * Medication/Prescription clinical card — fetches MedicationRequest resources and renders them.
 * Used for both Medications and Prescriptions cards (same FHIR resource, different title).
 */

import type { FhirMedicationRequest } from '../fhir-types';
import { useFhirSearch } from '../hooks/use-fhir-search';
import { adaptMedications } from '../adapters/medication-adapter';
import ClinicalCard from './ClinicalCard';

interface MedicationCardProps {
  readonly title: string;
  readonly fhirBaseUrl: string;
  readonly patientUuid: string;
  readonly webRoot: string;
}

export default function MedicationCard({ title, fhirBaseUrl, patientUuid, webRoot }: MedicationCardProps) {
  const { bundle, loading, error } = useFhirSearch<FhirMedicationRequest>(
    fhirBaseUrl,
    'MedicationRequest',
    patientUuid,
  );

  const items = bundle ? adaptMedications(bundle) : [];
  const editUrl = `${webRoot}/interface/patient_file/summary/demographics_legacy.php`;

  return (
    <ClinicalCard
      title={title}
      loading={loading}
      error={error}
      editUrl={editUrl}
      isEmpty={items.length === 0}
    >
      <ul className="clinical-card__list">
        {items.map((item) => (
          <li key={item.id || item.title} className="clinical-card__list-item">
            <div className="clinical-card__item-header">
              <strong>{item.title}</strong>
              <span className={`clinical-card__badge clinical-card__badge--${item.status}`}>
                {item.status}
              </span>
            </div>
            <div className="clinical-card__item-details">
              {item.dosage && <span>{item.dosage}</span>}
              {item.requester && <span>Prescribed by: {item.requester}</span>}
              {item.authoredOn && <span>Date: {item.authoredOn}</span>}
            </div>
          </li>
        ))}
      </ul>
    </ClinicalCard>
  );
}
