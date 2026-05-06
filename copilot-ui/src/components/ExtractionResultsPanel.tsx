/**
 * Extraction results panel (issue 011).
 *
 * Renders the structured output from POST /upload — either a lab table or a
 * collapsible intake form — alongside the chat. The shape arrives via the
 * `extraction` prop; the parent sets it after a successful upload.
 *
 * Confidence badges:
 *   high   → green
 *   medium → yellow
 *   low    → red
 *
 * Abnormal flags decorate the value cell of the lab table; reference range
 * is shown alongside so the clinician can verify the flag without leaving
 * the panel.
 */

import { useState, type JSX } from 'react';
import type {
  AbnormalFlag,
  Confidence,
  ExtractionResponse,
  IntakeAllergy,
  IntakeMedication,
  LabExtraction,
  LabResult,
} from '../api/extraction';

export interface ExtractionResultsPanelProps {
  readonly extraction: ExtractionResponse | null;
  readonly onDismiss?: () => void;
}

export function ExtractionResultsPanel(
  props: ExtractionResultsPanelProps,
): JSX.Element | null {
  const { extraction, onDismiss } = props;
  if (extraction === null) return null;

  return (
    <aside className="extraction-panel" aria-label="extraction results">
      <header className="extraction-panel__header">
        <div>
          <h3 className="extraction-panel__title">
            {extraction.doc_type === 'lab_pdf' ? 'Lab results' : 'Intake form'}
          </h3>
          <span className="extraction-panel__filename">
            {extraction.filename}
          </span>
        </div>
        {onDismiss ? (
          <button
            type="button"
            className="extraction-panel__close"
            aria-label="dismiss extraction"
            onClick={onDismiss}
          >
            ✕
          </button>
        ) : null}
      </header>

      {extraction.doc_type === 'lab_pdf' && extraction.lab ? (
        <LabPanel lab={extraction.lab} />
      ) : null}

      {extraction.doc_type === 'intake_form' && extraction.intake ? (
        <IntakePanel intake={extraction.intake} />
      ) : null}

      {extraction.doc_type === 'lab_pdf' && extraction.lab === null ? (
        <p className="extraction-panel__empty">
          No lab values were extracted from this document.
        </p>
      ) : null}
      {extraction.doc_type === 'intake_form' && extraction.intake === null ? (
        <p className="extraction-panel__empty">
          No intake form fields were extracted from this document.
        </p>
      ) : null}
    </aside>
  );
}

function LabPanel({ lab }: { readonly lab: LabExtraction }): JSX.Element {
  return (
    <div className="extraction-panel__lab">
      <dl className="extraction-panel__meta">
        {lab.patient_name ? (
          <>
            <dt>Patient</dt>
            <dd>{lab.patient_name}</dd>
          </>
        ) : null}
        {lab.collection_date ? (
          <>
            <dt>Collected</dt>
            <dd>{lab.collection_date}</dd>
          </>
        ) : null}
        {lab.lab_name ? (
          <>
            <dt>Lab</dt>
            <dd>{lab.lab_name}</dd>
          </>
        ) : null}
        {lab.ordering_provider ? (
          <>
            <dt>Provider</dt>
            <dd>{lab.ordering_provider}</dd>
          </>
        ) : null}
      </dl>

      {lab.results.length === 0 ? (
        <p className="extraction-panel__empty">No values found.</p>
      ) : (
        <table className="extraction-panel__table">
          <thead>
            <tr>
              <th scope="col">Test</th>
              <th scope="col">Value</th>
              <th scope="col">Reference</th>
              <th scope="col">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {lab.results.map((r, i) => (
              <LabRow key={`${r.test_name}-${i}`} result={r} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function LabRow({ result }: { readonly result: LabResult }): JSX.Element {
  return (
    <tr>
      <td>{result.test_name}</td>
      <td>
        <span
          className={`extraction-panel__value extraction-panel__value--${flagClass(result.abnormal_flag)}`}
          data-flag={result.abnormal_flag}
        >
          {result.value}
          {result.unit ? ` ${result.unit}` : ''}
        </span>
      </td>
      <td className="extraction-panel__range">
        {result.reference_range ?? '—'}
      </td>
      <td>
        <ConfidenceBadge confidence={result.confidence} />
      </td>
    </tr>
  );
}

function ConfidenceBadge(
  { confidence }: { readonly confidence: Confidence },
): JSX.Element {
  return (
    <span
      className={`extraction-panel__badge extraction-panel__badge--${confidence}`}
      data-confidence={confidence}
      aria-label={`confidence ${confidence}`}
    >
      {confidence}
    </span>
  );
}

function flagClass(flag: AbnormalFlag): string {
  if (flag === 'high' || flag === 'critical') return 'high';
  if (flag === 'low') return 'low';
  return 'normal';
}

function IntakePanel(
  { intake }: { readonly intake: import('../api/extraction').IntakeExtraction },
): JSX.Element {
  return (
    <div className="extraction-panel__intake">
      {intake.chief_concern ? (
        <Section title="Chief concern" defaultOpen>
          <p className="extraction-panel__cc">{intake.chief_concern}</p>
        </Section>
      ) : null}

      <Section title="Demographics" defaultOpen>
        <dl className="extraction-panel__meta">
          {intake.demographics.name ? (
            <>
              <dt>Name</dt>
              <dd>{intake.demographics.name}</dd>
            </>
          ) : null}
          {intake.demographics.date_of_birth ? (
            <>
              <dt>DOB</dt>
              <dd>{intake.demographics.date_of_birth}</dd>
            </>
          ) : null}
          {intake.demographics.sex ? (
            <>
              <dt>Sex</dt>
              <dd>{intake.demographics.sex}</dd>
            </>
          ) : null}
          {intake.demographics.phone ? (
            <>
              <dt>Phone</dt>
              <dd>{intake.demographics.phone}</dd>
            </>
          ) : null}
          {intake.demographics.address ? (
            <>
              <dt>Address</dt>
              <dd>{intake.demographics.address}</dd>
            </>
          ) : null}
        </dl>
      </Section>

      <Section title={`Medications (${intake.current_medications.length})`}>
        {intake.current_medications.length === 0 ? (
          <p className="extraction-panel__empty">None reported.</p>
        ) : (
          <ul className="extraction-panel__list">
            {intake.current_medications.map((m, i) => (
              <MedicationRow key={`${m.name}-${i}`} med={m} />
            ))}
          </ul>
        )}
      </Section>

      <Section title={`Allergies (${intake.allergies.length})`}>
        {intake.allergies.length === 0 ? (
          <p className="extraction-panel__empty">None reported.</p>
        ) : (
          <ul className="extraction-panel__list">
            {intake.allergies.map((a, i) => (
              <AllergyRow key={`${a.substance}-${i}`} allergy={a} />
            ))}
          </ul>
        )}
      </Section>

      {intake.family_history.length > 0 ? (
        <Section title={`Family history (${intake.family_history.length})`}>
          <ul className="extraction-panel__list">
            {intake.family_history.map((f, i) => (
              <li key={`${f.relation}-${i}`}>
                <span className="extraction-panel__list-primary">
                  {f.relation}: {f.condition}
                </span>
                <ConfidenceBadge confidence={f.confidence} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {intake.social_history ? (
        <Section title="Social history">
          <dl className="extraction-panel__meta">
            {intake.social_history.tobacco ? (
              <>
                <dt>Tobacco</dt>
                <dd>{intake.social_history.tobacco}</dd>
              </>
            ) : null}
            {intake.social_history.alcohol ? (
              <>
                <dt>Alcohol</dt>
                <dd>{intake.social_history.alcohol}</dd>
              </>
            ) : null}
            {intake.social_history.substance_use ? (
              <>
                <dt>Other substances</dt>
                <dd>{intake.social_history.substance_use}</dd>
              </>
            ) : null}
            {intake.social_history.occupation ? (
              <>
                <dt>Occupation</dt>
                <dd>{intake.social_history.occupation}</dd>
              </>
            ) : null}
          </dl>
        </Section>
      ) : null}
    </div>
  );
}

function MedicationRow({ med }: { readonly med: IntakeMedication }): JSX.Element {
  const detail = [med.dose, med.frequency].filter((s) => s).join(' · ');
  return (
    <li>
      <span className="extraction-panel__list-primary">{med.name}</span>
      {detail ? (
        <span className="extraction-panel__list-secondary"> {detail}</span>
      ) : null}
      <ConfidenceBadge confidence={med.confidence} />
    </li>
  );
}

function AllergyRow({ allergy }: { readonly allergy: IntakeAllergy }): JSX.Element {
  const detail = [allergy.reaction, allergy.severity].filter((s) => s).join(' · ');
  return (
    <li>
      <span className="extraction-panel__list-primary">{allergy.substance}</span>
      {detail ? (
        <span className="extraction-panel__list-secondary"> {detail}</span>
      ) : null}
      <ConfidenceBadge confidence={allergy.confidence} />
    </li>
  );
}

function Section({
  title,
  defaultOpen = false,
  children,
}: {
  readonly title: string;
  readonly defaultOpen?: boolean;
  readonly children: React.ReactNode;
}): JSX.Element {
  const [open, setOpen] = useState<boolean>(defaultOpen);
  return (
    <section className="extraction-panel__section">
      <button
        type="button"
        className="extraction-panel__section-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">{open ? '▾' : '▸'}</span> {title}
      </button>
      {open ? (
        <div className="extraction-panel__section-body">{children}</div>
      ) : null}
    </section>
  );
}
