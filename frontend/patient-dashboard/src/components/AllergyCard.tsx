/**
 * Allergies clinical card — fetches AllergyIntolerance resources and renders them.
 */

import type { FhirAllergyIntolerance } from '../fhir-types';
import { useFhirSearch } from '../hooks/use-fhir-search';
import { adaptAllergies } from '../adapters/allergy-adapter';
import ClinicalCard from './ClinicalCard';

interface AllergyCardProps {
  readonly fhirBaseUrl: string;
  readonly patientUuid: string;
  readonly webRoot: string;
}

export default function AllergyCard({ fhirBaseUrl, patientUuid, webRoot }: AllergyCardProps) {
  const { bundle, loading, error } = useFhirSearch<FhirAllergyIntolerance>(
    fhirBaseUrl,
    'AllergyIntolerance',
    patientUuid,
  );

  const items = bundle ? adaptAllergies(bundle) : [];
  const editUrl = `${webRoot}/interface/patient_file/summary/demographics_legacy.php`;

  return (
    <ClinicalCard
      title="Allergies"
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
              {item.criticality !== 'unknown' && (
                <span className={`clinical-card__badge clinical-card__badge--${item.criticality}`}>
                  {item.criticality}
                </span>
              )}
            </div>
            <div className="clinical-card__item-details">
              {item.category !== 'unknown' && <span>Category: {item.category}</span>}
              {item.reaction && <span>Reaction: {item.reaction}</span>}
              {item.recordedDate && <span>Recorded: {item.recordedDate}</span>}
            </div>
          </li>
        ))}
      </ul>
    </ClinicalCard>
  );
}
