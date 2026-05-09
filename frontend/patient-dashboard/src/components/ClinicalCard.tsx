/**
 * Shared card shell for clinical data cards (allergies, problems, medications, etc.).
 * Handles loading, error, and empty states uniformly.
 */

import type { ReactNode } from 'react';

interface ClinicalCardProps {
  readonly title: string;
  readonly loading: boolean;
  readonly error: string | null;
  readonly editUrl: string;
  readonly isEmpty?: boolean;
  readonly children: ReactNode;
}

export default function ClinicalCard({
  title,
  loading,
  error,
  editUrl,
  isEmpty,
  children,
}: ClinicalCardProps) {
  return (
    <section className="clinical-card" data-testid={`card-${title.toLowerCase().replace(/\s+/g, '-')}`}>
      <div className="clinical-card__header">
        <h3 className="clinical-card__title">{title}</h3>
        <a href={editUrl} className="clinical-card__edit-link">
          Edit in OpenEMR
        </a>
      </div>
      <div className="clinical-card__body">
        {loading && (
          <div className="clinical-card__loading" role="status">
            <span>Loading {title.toLowerCase()}...</span>
          </div>
        )}
        {!loading && error && (
          <div className="clinical-card__error" role="alert">
            <span>Unable to load {title.toLowerCase()}: {error}</span>
          </div>
        )}
        {!loading && !error && isEmpty && (
          <p className="clinical-card__empty">No {title.toLowerCase()} recorded.</p>
        )}
        {!loading && !error && !isEmpty && children}
      </div>
    </section>
  );
}
