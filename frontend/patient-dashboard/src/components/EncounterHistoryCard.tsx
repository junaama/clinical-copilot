/**
 * Encounter History clinical card — fetches Encounter resources and renders them.
 */

import type { FhirEncounter } from '../fhir-types';
import { useFhirSearch } from '../hooks/use-fhir-search';
import { adaptEncounters } from '../adapters/encounter-adapter';
import ClinicalCard from './ClinicalCard';

interface EncounterHistoryCardProps {
  readonly fhirBaseUrl: string;
  readonly patientUuid: string;
  readonly webRoot: string;
}

export default function EncounterHistoryCard({ fhirBaseUrl, patientUuid, webRoot }: EncounterHistoryCardProps) {
  const { bundle, loading, error } = useFhirSearch<FhirEncounter>(
    fhirBaseUrl,
    'Encounter',
    patientUuid,
  );

  const items = bundle ? adaptEncounters(bundle) : [];
  const editUrl = `${webRoot}/interface/patient_file/summary/demographics_legacy.php`;

  return (
    <ClinicalCard
      title="Encounter History"
      loading={loading}
      error={error}
      editUrl={editUrl}
      isEmpty={items.length === 0}
    >
      <ul className="clinical-card__list">
        {items.map((item) => (
          <li key={item.id || item.type} className="clinical-card__list-item">
            <div className="clinical-card__item-header">
              <strong>{item.type}</strong>
              <span className={`clinical-card__badge clinical-card__badge--${item.status}`}>
                {item.status}
              </span>
            </div>
            <div className="clinical-card__item-details">
              {item.startDate && <span>Date: {item.startDate}</span>}
              {item.reason && <span>Reason: {item.reason}</span>}
            </div>
          </li>
        ))}
      </ul>
    </ClinicalCard>
  );
}
