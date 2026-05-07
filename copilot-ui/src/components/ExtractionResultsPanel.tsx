/**
 * Extraction results panel (issue 011, source tabs issue 032).
 *
 * Renders the structured output from POST /upload alongside the chat. The
 * panel exposes two tabs:
 *
 *   - Results: structured extraction (lab table or intake form sections),
 *     with a "show source" CTA on clinically important fields whose
 *     ``field_path`` has an exact match in the upload-time bbox map.
 *   - Source: an in-browser preview of the uploaded document with the
 *     extraction's drawable bboxes overlaid. The selected box is rendered
 *     prominently; the rest are visible but quieter so the broader
 *     extraction coverage is legible at a glance.
 *
 * The Source tab works directly off the browser-local ``File`` the parent
 * carried forward from the upload widget — no new backend download
 * endpoint is needed for the submission pass. PDFs are accepted but the
 * page-rendered preview is deferred to issue 033; this implementation
 * handles image uploads end-to-end.
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

import { useEffect, useMemo, useState, type CSSProperties, type JSX } from 'react';
import type {
  AbnormalFlag,
  Confidence,
  ExtractionResponse,
  IntakeAllergy,
  IntakeMedication,
  LabExtraction,
  LabResult,
  UploadBboxRecord,
} from '../api/extraction';

export interface ExtractionResultsPanelProps {
  readonly extraction: ExtractionResponse | null;
  readonly onDismiss?: () => void;
  /**
   * Browser-local uploaded file. Carried forward from the upload widget so
   * the Source tab can render a preview without a new backend endpoint.
   * Optional — when null/undefined, the Source tab renders an empty state.
   */
  readonly sourceFile?: File | null;
}

type TabKey = 'results' | 'source';

interface SourceContext {
  readonly byPath: ReadonlyMap<string, UploadBboxRecord>;
  readonly selectedFieldPath: string | null;
  readonly onShow: (fieldPath: string) => void;
}

export function ExtractionResultsPanel(
  props: ExtractionResultsPanelProps,
): JSX.Element | null {
  const { extraction, onDismiss, sourceFile = null } = props;
  const [activeTab, setActiveTab] = useState<TabKey>('results');
  const [selectedFieldPath, setSelectedFieldPath] = useState<string | null>(
    null,
  );

  const bboxes: readonly UploadBboxRecord[] = extraction?.bboxes ?? [];
  const byPath = useMemo<ReadonlyMap<string, UploadBboxRecord>>(() => {
    const map = new Map<string, UploadBboxRecord>();
    for (const record of bboxes) {
      // Last-write-wins on duplicate field_paths — backend filters
      // upstream, but the Map keeps lookup deterministic.
      map.set(record.field_path, record);
    }
    return map;
  }, [bboxes]);

  if (extraction === null) return null;
  // Issue 025: a non-ok canonical outcome must never render as an empty
  // successful extraction. The app shell already gates ``setExtraction``
  // on ``status === 'ok'``; this is defense-in-depth so a stale or
  // out-of-band failure outcome can't paint over a real one.
  if (extraction.status !== 'ok' || extraction.discussable !== true) {
    return null;
  }
  const effectiveType: import('../api/extraction').DocType =
    extraction.effective_type ?? extraction.doc_type;

  const sourceCtx: SourceContext = {
    byPath,
    selectedFieldPath,
    onShow: (fieldPath: string): void => {
      setSelectedFieldPath(fieldPath);
      setActiveTab('source');
    },
  };

  return (
    <aside className="extraction-panel" aria-label="extraction results">
      <header className="extraction-panel__header">
        <div>
          <h3 className="extraction-panel__title">
            {effectiveType === 'lab_pdf' ? 'Lab results' : 'Intake form'}
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

      <div className="extraction-panel__tabs" role="tablist" aria-label="extraction view">
        <TabButton
          tabKey="results"
          activeTab={activeTab}
          onSelect={setActiveTab}
          label="Results"
        />
        <TabButton
          tabKey="source"
          activeTab={activeTab}
          onSelect={setActiveTab}
          label="Source"
        />
      </div>

      {activeTab === 'results' ? (
        <div
          role="tabpanel"
          aria-labelledby="extraction-tab-results"
          data-testid="results-tab-panel"
        >
          {effectiveType === 'lab_pdf' && extraction.lab ? (
            <LabPanel lab={extraction.lab} sourceCtx={sourceCtx} />
          ) : null}

          {effectiveType === 'intake_form' && extraction.intake ? (
            <IntakePanel intake={extraction.intake} sourceCtx={sourceCtx} />
          ) : null}

          {effectiveType === 'lab_pdf' && extraction.lab === null ? (
            <p className="extraction-panel__empty">
              No lab values were extracted from this document.
            </p>
          ) : null}
          {effectiveType === 'intake_form' && extraction.intake === null ? (
            <p className="extraction-panel__empty">
              No intake form fields were extracted from this document.
            </p>
          ) : null}
        </div>
      ) : (
        <SourceTab
          file={sourceFile}
          bboxes={bboxes}
          selectedFieldPath={selectedFieldPath}
        />
      )}
    </aside>
  );
}

interface TabButtonProps {
  readonly tabKey: TabKey;
  readonly activeTab: TabKey;
  readonly onSelect: (key: TabKey) => void;
  readonly label: string;
}

function TabButton({
  tabKey,
  activeTab,
  onSelect,
  label,
}: TabButtonProps): JSX.Element {
  const selected = activeTab === tabKey;
  return (
    <button
      type="button"
      role="tab"
      id={`extraction-tab-${tabKey}`}
      aria-selected={selected}
      className={
        selected
          ? 'extraction-panel__tab extraction-panel__tab--active'
          : 'extraction-panel__tab'
      }
      onClick={() => onSelect(tabKey)}
    >
      {label}
    </button>
  );
}

function LabPanel({
  lab,
  sourceCtx,
}: {
  readonly lab: LabExtraction;
  readonly sourceCtx: SourceContext;
}): JSX.Element {
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
              <th scope="col" aria-label="source" />
            </tr>
          </thead>
          <tbody>
            {lab.results.map((r, i) => (
              <LabRow
                key={`${r.test_name}-${i}`}
                result={r}
                fieldPath={`results[${i}].value`}
                sourceCtx={sourceCtx}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function LabRow({
  result,
  fieldPath,
  sourceCtx,
}: {
  readonly result: LabResult;
  readonly fieldPath: string;
  readonly sourceCtx: SourceContext;
}): JSX.Element {
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
      <td>
        <SourceCta fieldPath={fieldPath} sourceCtx={sourceCtx} />
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

function IntakePanel({
  intake,
  sourceCtx,
}: {
  readonly intake: import('../api/extraction').IntakeExtraction;
  readonly sourceCtx: SourceContext;
}): JSX.Element {
  return (
    <div className="extraction-panel__intake">
      {intake.chief_concern ? (
        <Section title="Chief concern" defaultOpen>
          <p className="extraction-panel__cc">
            {intake.chief_concern}{' '}
            <SourceCta fieldPath="chief_concern" sourceCtx={sourceCtx} />
          </p>
        </Section>
      ) : null}

      <Section title="Demographics" defaultOpen>
        <dl className="extraction-panel__meta">
          {intake.demographics.name ? (
            <>
              <dt>Name</dt>
              <dd>
                {intake.demographics.name}{' '}
                <SourceCta
                  fieldPath="demographics.name"
                  sourceCtx={sourceCtx}
                />
              </dd>
            </>
          ) : null}
          {intake.demographics.date_of_birth ? (
            <>
              <dt>DOB</dt>
              <dd>
                {intake.demographics.date_of_birth}{' '}
                <SourceCta
                  fieldPath="demographics.date_of_birth"
                  sourceCtx={sourceCtx}
                />
              </dd>
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
              <MedicationRow
                key={`${m.name}-${i}`}
                med={m}
                fieldPath={`current_medications[${i}].name`}
                sourceCtx={sourceCtx}
              />
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
              <AllergyRow
                key={`${a.substance}-${i}`}
                allergy={a}
                fieldPath={`allergies[${i}].substance`}
                sourceCtx={sourceCtx}
              />
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
                <SourceCta
                  fieldPath={`family_history[${i}].condition`}
                  sourceCtx={sourceCtx}
                />
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

function MedicationRow({
  med,
  fieldPath,
  sourceCtx,
}: {
  readonly med: IntakeMedication;
  readonly fieldPath: string;
  readonly sourceCtx: SourceContext;
}): JSX.Element {
  const detail = [med.dose, med.frequency].filter((s) => s).join(' · ');
  return (
    <li>
      <span className="extraction-panel__list-primary">{med.name}</span>
      {detail ? (
        <span className="extraction-panel__list-secondary"> {detail}</span>
      ) : null}
      <ConfidenceBadge confidence={med.confidence} />
      <SourceCta fieldPath={fieldPath} sourceCtx={sourceCtx} />
    </li>
  );
}

function AllergyRow({
  allergy,
  fieldPath,
  sourceCtx,
}: {
  readonly allergy: IntakeAllergy;
  readonly fieldPath: string;
  readonly sourceCtx: SourceContext;
}): JSX.Element {
  const detail = [allergy.reaction, allergy.severity].filter((s) => s).join(' · ');
  return (
    <li>
      <span className="extraction-panel__list-primary">{allergy.substance}</span>
      {detail ? (
        <span className="extraction-panel__list-secondary"> {detail}</span>
      ) : null}
      <ConfidenceBadge confidence={allergy.confidence} />
      <SourceCta fieldPath={fieldPath} sourceCtx={sourceCtx} />
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

/**
 * "Show source" affordance. Renders only when the field's exact path has a
 * matching bbox record — fields without a drawable match never expose a
 * source CTA, per the issue 032 acceptance contract.
 */
function SourceCta({
  fieldPath,
  sourceCtx,
}: {
  readonly fieldPath: string;
  readonly sourceCtx: SourceContext;
}): JSX.Element | null {
  if (!sourceCtx.byPath.has(fieldPath)) return null;
  return (
    <button
      type="button"
      className="extraction-panel__source-cta"
      data-testid={`source-cta-${fieldPath}`}
      aria-label={`Show source for ${fieldPath}`}
      onClick={() => sourceCtx.onShow(fieldPath)}
    >
      show source
    </button>
  );
}

interface SourceTabProps {
  readonly file: File | null;
  readonly bboxes: readonly UploadBboxRecord[];
  readonly selectedFieldPath: string | null;
}

function SourceTab({
  file,
  bboxes,
  selectedFieldPath,
}: SourceTabProps): JSX.Element {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (file === null) {
      setObjectUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setObjectUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [file]);

  // The selected bbox dictates which page is in view for paged documents.
  // For images there is only one "page" so the selected page is implicit.
  const selectedBbox: UploadBboxRecord | null = useMemo(() => {
    if (selectedFieldPath === null) return null;
    return bboxes.find((b) => b.field_path === selectedFieldPath) ?? null;
  }, [selectedFieldPath, bboxes]);

  const visiblePage = selectedBbox?.bbox.page ?? 1;
  const pageBboxes = bboxes.filter((b) => b.bbox.page === visiblePage);

  if (file === null) {
    return (
      <div
        role="tabpanel"
        aria-labelledby="extraction-tab-source"
        data-testid="source-tab-panel"
      >
        <p className="extraction-panel__empty">
          No source preview available — the uploaded file is not in browser
          memory for this conversation.
        </p>
      </div>
    );
  }

  const isImage = file.type.startsWith('image/');

  return (
    <div
      role="tabpanel"
      aria-labelledby="extraction-tab-source"
      data-testid="source-tab-panel"
      className="extraction-panel__source"
    >
      <div className="extraction-panel__source-stage">
        {isImage && objectUrl !== null ? (
          <img
            src={objectUrl}
            alt="source preview"
            className="extraction-panel__source-image"
          />
        ) : null}
        {!isImage ? (
          <div
            className="extraction-panel__source-placeholder"
            data-testid="source-pdf-placeholder"
          >
            <p>
              PDF source viewer is being assembled — image previews are
              available now and PDFs will join in a follow-up.
            </p>
          </div>
        ) : null}
        <div
          className="extraction-panel__source-overlay"
          aria-hidden="true"
          data-page={visiblePage}
        >
          {pageBboxes.map((record) => (
            <BboxOverlay
              key={record.field_path}
              record={record}
              selected={record.field_path === selectedFieldPath}
            />
          ))}
        </div>
      </div>
      <p className="extraction-panel__source-caption">
        Page {visiblePage}
        {selectedBbox ? ` · highlighting ${selectedBbox.field_path}` : ''}
      </p>
    </div>
  );
}

function BboxOverlay({
  record,
  selected,
}: {
  readonly record: UploadBboxRecord;
  readonly selected: boolean;
}): JSX.Element {
  const style: CSSProperties = {
    position: 'absolute',
    left: `${record.bbox.x * 100}%`,
    top: `${record.bbox.y * 100}%`,
    width: `${record.bbox.width * 100}%`,
    height: `${record.bbox.height * 100}%`,
    pointerEvents: 'none',
  };
  // The selected box gets a stable ``source-bbox-selected`` testid so
  // tests can assert on "the highlighted one" without knowing the field
  // path. The data-field-path attribute carries the identity for both
  // selected and unselected boxes.
  const testId = selected
    ? 'source-bbox-selected'
    : `source-bbox-${record.field_path}`;
  return (
    <div
      data-testid={testId}
      data-field-path={record.field_path}
      data-selected={selected ? 'true' : 'false'}
      className={
        selected
          ? 'extraction-panel__bbox extraction-panel__bbox--selected'
          : 'extraction-panel__bbox extraction-panel__bbox--faint'
      }
      style={style}
    />
  );
}
