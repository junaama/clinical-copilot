/**
 * Problem List clinical card — fetches Condition resources and renders them.
 */

import type { FhirCondition } from '../fhir-types';
import { useFhirSearch } from '../hooks/use-fhir-search';
import { adaptConditions } from '../adapters/condition-adapter';
import ClinicalCard from './ClinicalCard';

interface ProblemListCardProps {
  readonly fhirBaseUrl: string;
  readonly patientUuid: string;
  readonly webRoot: string;
}

export default function ProblemListCard({ fhirBaseUrl, patientUuid, webRoot }: ProblemListCardProps) {
  const { bundle, loading, error } = useFhirSearch<FhirCondition>(
    fhirBaseUrl,
    'Condition',
    patientUuid,
  );

  const items = bundle ? adaptConditions(bundle) : [];
  const editUrl = `${webRoot}/interface/patient_file/summary/demographics_legacy.php`;

  return (
    <ClinicalCard
      title="Problem List"
      loading={loading}
      error={error}
      editUrl={editUrl}
      isEmpty={items.length === 0}
    >
      <ul className="clinical-card__list">
        {items.map((item) => (
          <li key={item.id || item.title} className="clinical-card__list-item">
            <div className="clinical-card__item-header">
              <div className="clinical-card__item-heading">
                <strong>{item.title}</strong>
                {item.titleQualifier && (
                  <span className="clinical-card__badge clinical-card__badge--qualifier">
                    {item.titleQualifier}
                  </span>
                )}
              </div>
              <span className={`clinical-card__badge clinical-card__badge--${item.clinicalStatus}`}>
                {item.clinicalStatus}
              </span>
            </div>
            {item.onsetDate && (
              <div className="clinical-card__item-details">
                <span>Onset: {item.onsetDate}</span>
              </div>
            )}
          </li>
        ))}
      </ul>
    </ClinicalCard>
  );
}
