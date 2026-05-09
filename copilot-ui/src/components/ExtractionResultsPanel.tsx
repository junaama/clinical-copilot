/**
 * Extraction results panel (issue 011, source tabs issue 032, PDF viewer
 * issue 033).
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
 * endpoint is needed. Image uploads render via an ``<img>`` element and
 * a normalized-coordinate overlay; PDFs render via pdfjs-dist into a
 * canvas, with the same normalized overlay model applied to the page
 * the selected bbox lives on.
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

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type JSX,
} from 'react';
import type {
  AbnormalFlag,
  AdtExtraction,
  Confidence,
  ExtractionResponse,
  IntakeAllergy,
  IntakeMedication,
  LabExtraction,
  LabResult,
  ReferralExtraction,
  ReferralLab,
  UploadBboxRecord,
} from '../api/extraction';
import { renderPdfPageToCanvas } from '../lib/pdfRenderer';

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
  const labLike = isLabDocType(effectiveType);

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
            {extractionTitle(effectiveType)}
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
          {labLike && extraction.lab ? (
            <LabPanel lab={extraction.lab} sourceCtx={sourceCtx} />
          ) : null}

          {effectiveType === 'intake_form' && extraction.intake ? (
            <IntakePanel intake={extraction.intake} sourceCtx={sourceCtx} />
          ) : null}

          {effectiveType === 'docx_referral' && extraction.referral ? (
            <ReferralPanel referral={extraction.referral} />
          ) : null}

          {effectiveType === 'hl7_adt' && extraction.adt ? (
            <AdtPanel adt={extraction.adt} />
          ) : null}

          {labLike && extraction.lab === null ? (
            <p className="extraction-panel__empty">
              No lab values were extracted from this document.
            </p>
          ) : null}
          {effectiveType === 'intake_form' && extraction.intake === null ? (
            <p className="extraction-panel__empty">
              No intake form fields were extracted from this document.
            </p>
          ) : null}
          {effectiveType === 'docx_referral' && !extraction.referral ? (
            <p className="extraction-panel__empty">
              No referral fields were extracted from this document.
            </p>
          ) : null}
          {effectiveType === 'hl7_adt' && !extraction.adt ? (
            <p className="extraction-panel__empty">
              No ADT details were extracted from this document.
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

function extractionTitle(docType: import('../api/extraction').DocType): string {
  if (isLabDocType(docType)) {
    return 'Lab results';
  }
  if (docType === 'hl7_adt') return 'HL7 ADT update';
  if (docType === 'docx_referral') return 'Referral letter';
  return 'Intake form';
}

function isLabDocType(docType: import('../api/extraction').DocType): boolean {
  return (
    docType === 'lab_pdf' ||
    docType === 'hl7_oru' ||
    docType === 'xlsx_workbook' ||
    docType === 'tiff_fax'
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
      <Section title="Chief concern" defaultOpen>
        <p className="extraction-panel__cc">
          {intake.chief_concern}{' '}
          <SourceCta fieldPath="chief_concern" sourceCtx={sourceCtx} />
        </p>
      </Section>

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
          {intake.demographics.dob ? (
            <>
              <dt>DOB</dt>
              <dd>
                {intake.demographics.dob}{' '}
                <SourceCta
                  fieldPath="demographics.dob"
                  sourceCtx={sourceCtx}
                />
              </dd>
            </>
          ) : null}
          {intake.demographics.gender ? (
            <>
              <dt>Gender</dt>
              <dd>
                {intake.demographics.gender}{' '}
                <SourceCta
                  fieldPath="demographics.gender"
                  sourceCtx={sourceCtx}
                />
              </dd>
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
          {intake.demographics.emergency_contact ? (
            <>
              <dt>Emergency contact</dt>
              <dd>{intake.demographics.emergency_contact}</dd>
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
            {intake.social_history.smoking ? (
              <>
                <dt>Smoking</dt>
                <dd>{intake.social_history.smoking}</dd>
              </>
            ) : null}
            {intake.social_history.alcohol ? (
              <>
                <dt>Alcohol</dt>
                <dd>{intake.social_history.alcohol}</dd>
              </>
            ) : null}
            {intake.social_history.drugs ? (
              <>
                <dt>Drugs</dt>
                <dd>{intake.social_history.drugs}</dd>
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
  const detail = [med.dose, med.frequency, med.prescriber]
    .filter((s) => s)
    .join(' · ');
  return (
    <li>
      <span className="extraction-panel__list-primary">{med.name}</span>
      {detail ? (
        <span className="extraction-panel__list-secondary"> {detail}</span>
      ) : null}
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
      <SourceCta fieldPath={fieldPath} sourceCtx={sourceCtx} />
    </li>
  );
}

function ReferralPanel({
  referral,
}: {
  readonly referral: ReferralExtraction;
}): JSX.Element {
  const identifiers = Object.entries(referral.patient_identifiers);
  return (
    <div className="extraction-panel__intake">
      <Section title="Referral" defaultOpen>
        <dl className="extraction-panel__meta">
          {referral.referring_provider ? (
            <>
              <dt>From</dt>
              <dd>{referral.referring_provider}</dd>
            </>
          ) : null}
          {referral.referring_organization ? (
            <>
              <dt>Organization</dt>
              <dd>{referral.referring_organization}</dd>
            </>
          ) : null}
          {referral.receiving_provider ? (
            <>
              <dt>To</dt>
              <dd>{referral.receiving_provider}</dd>
            </>
          ) : null}
          {referral.receiving_organization ? (
            <>
              <dt>Receiving org</dt>
              <dd>{referral.receiving_organization}</dd>
            </>
          ) : null}
        </dl>
      </Section>

      <Section title="Patient" defaultOpen>
        <dl className="extraction-panel__meta">
          {referral.patient_name ? (
            <>
              <dt>Name</dt>
              <dd>{referral.patient_name}</dd>
            </>
          ) : null}
          {referral.patient_dob ? (
            <>
              <dt>DOB</dt>
              <dd>{referral.patient_dob}</dd>
            </>
          ) : null}
          {identifiers.map(([key, value]) => (
            <FragmentPair key={key} label={key} value={value} />
          ))}
        </dl>
      </Section>

      {referral.reason_for_referral ? (
        <Section title="Reason" defaultOpen>
          <p className="extraction-panel__cc">{referral.reason_for_referral}</p>
        </Section>
      ) : null}

      {referral.pertinent_history ? (
        <Section title="Pertinent history">
          <p className="extraction-panel__cc">{referral.pertinent_history}</p>
        </Section>
      ) : null}

      <ReferralList title="Medical history" items={referral.past_medical_history} />
      <ReferralList title="Medications" items={referral.current_medications} />
      <ReferralList title="Allergies" items={referral.allergies} emptyText="None reported." />

      {referral.pertinent_labs.length > 0 ? (
        <Section title={`Pertinent labs (${referral.pertinent_labs.length})`}>
          <table className="extraction-panel__table">
            <thead>
              <tr>
                <th scope="col">Test</th>
                <th scope="col">Value</th>
                <th scope="col">Date</th>
              </tr>
            </thead>
            <tbody>
              {referral.pertinent_labs.map((lab, i) => (
                <ReferralLabRow key={`${lab.name}-${i}`} lab={lab} />
              ))}
            </tbody>
          </table>
        </Section>
      ) : null}

      {referral.requested_action ? (
        <Section title="Requested action" defaultOpen>
          <p className="extraction-panel__cc">{referral.requested_action}</p>
        </Section>
      ) : null}
    </div>
  );
}

function FragmentPair({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}): JSX.Element {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function ReferralList({
  title,
  items,
  emptyText = 'None found.',
}: {
  readonly title: string;
  readonly items: readonly string[];
  readonly emptyText?: string;
}): JSX.Element {
  return (
    <Section title={`${title} (${items.length})`}>
      {items.length === 0 ? (
        <p className="extraction-panel__empty">{emptyText}</p>
      ) : (
        <ul className="extraction-panel__list">
          {items.map((item, i) => (
            <li key={`${item}-${i}`}>
              <span className="extraction-panel__list-primary">{item}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function ReferralLabRow({
  lab,
}: {
  readonly lab: ReferralLab;
}): JSX.Element {
  const value = `${lab.value}${lab.unit ? ` ${lab.unit}` : ''}`;
  return (
    <tr>
      <td>{lab.name}</td>
      <td>
        <span
          className={
            lab.flag
              ? 'extraction-panel__value extraction-panel__value--high'
              : 'extraction-panel__value extraction-panel__value--normal'
          }
        >
          {value}
          {lab.flag ? ` ${lab.flag}` : ''}
        </span>
      </td>
      <td className="extraction-panel__range">{lab.collection_date ?? '-'}</td>
    </tr>
  );
}

function AdtPanel({
  adt,
}: {
  readonly adt: AdtExtraction;
}): JSX.Element {
  const meta = adt.message_metadata;
  const demo = adt.patient_demographics;
  const visit = adt.visit;
  const insurance = adt.insurance ?? [];
  return (
    <div className="extraction-panel__intake">
      <Section title="Patient" defaultOpen>
        <dl className="extraction-panel__meta">
          {demo.name ? <FragmentPair label="Name" value={demo.name} /> : null}
          {demo.dob ? <FragmentPair label="DOB" value={demo.dob} /> : null}
          {demo.gender ? <FragmentPair label="Gender" value={demo.gender} /> : null}
          {demo.phone ? <FragmentPair label="Phone" value={demo.phone} /> : null}
          {demo.address ? <FragmentPair label="Address" value={demo.address} /> : null}
        </dl>
      </Section>

      <Section title="Message" defaultOpen>
        <dl className="extraction-panel__meta">
          {meta.trigger_event ? (
            <FragmentPair label="Trigger" value={meta.trigger_event} />
          ) : null}
          {meta.event_reason ? (
            <FragmentPair label="Reason" value={meta.event_reason} />
          ) : null}
          {meta.sending_facility ? (
            <FragmentPair label="Facility" value={meta.sending_facility} />
          ) : null}
          {meta.event_datetime ? (
            <FragmentPair label="Event time" value={meta.event_datetime} />
          ) : null}
        </dl>
      </Section>

      {visit ? (
        <Section title="Encounter" defaultOpen>
          <dl className="extraction-panel__meta">
            {visit.patient_class ? (
              <FragmentPair label="Class" value={visit.patient_class} />
            ) : null}
            {visit.location ? (
              <FragmentPair label="Location" value={visit.location} />
            ) : null}
            {visit.attending_provider ? (
              <FragmentPair label="Attending" value={visit.attending_provider} />
            ) : null}
            {visit.visit_number ? (
              <FragmentPair label="Visit" value={visit.visit_number} />
            ) : null}
          </dl>
        </Section>
      ) : null}

      {insurance.length > 0 ? (
        <Section title={`Insurance (${insurance.length})`} defaultOpen>
          <ul className="extraction-panel__list">
            {insurance.map((plan, i) => (
              <li key={`${plan.company_name ?? 'plan'}-${i}`}>
                <span className="extraction-panel__list-primary">
                  {plan.company_name ?? 'Insurance plan'}
                </span>
                {plan.member_id || plan.plan_type ? (
                  <span className="extraction-panel__list-secondary">
                    {' '}
                    {[plan.member_id, plan.plan_type].filter((s) => s).join(' · ')}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
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
  const isPdf =
    file.type === 'application/pdf' ||
    file.name.toLowerCase().endsWith('.pdf');

  return (
    <div
      role="tabpanel"
      aria-labelledby="extraction-tab-source"
      data-testid="source-tab-panel"
      className="extraction-panel__source"
    >
      {isImage && objectUrl !== null ? (
        <ImageStage
          objectUrl={objectUrl}
          pageBboxes={pageBboxes}
          selectedFieldPath={selectedFieldPath}
          visiblePage={visiblePage}
          captionSuffix={
            selectedBbox ? ` · highlighting ${selectedBbox.field_path}` : ''
          }
        />
      ) : isPdf ? (
        <PdfStage
          file={file}
          page={visiblePage}
          pageBboxes={pageBboxes}
          selectedFieldPath={selectedFieldPath}
          selectedBbox={selectedBbox}
        />
      ) : (
        <div className="extraction-panel__empty">
          <p>This document type does not support a source preview yet.</p>
        </div>
      )}
    </div>
  );
}

interface ImageStageProps {
  readonly objectUrl: string;
  readonly pageBboxes: readonly UploadBboxRecord[];
  readonly selectedFieldPath: string | null;
  readonly visiblePage: number;
  readonly captionSuffix: string;
}

function ImageStage({
  objectUrl,
  pageBboxes,
  selectedFieldPath,
  visiblePage,
  captionSuffix,
}: ImageStageProps): JSX.Element {
  return (
    <>
      <div className="extraction-panel__source-stage">
        <img
          src={objectUrl}
          alt="source preview"
          className="extraction-panel__source-image"
        />
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
        {captionSuffix}
      </p>
    </>
  );
}

interface PdfStageProps {
  readonly file: File;
  readonly page: number;
  readonly pageBboxes: readonly UploadBboxRecord[];
  readonly selectedFieldPath: string | null;
  readonly selectedBbox: UploadBboxRecord | null;
}

type PdfRenderState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'rendering' }
  | { readonly kind: 'ready'; readonly numPages: number; readonly renderedPage: number }
  | { readonly kind: 'error' };

function PdfStage({
  file,
  page,
  pageBboxes,
  selectedFieldPath,
  selectedBbox,
}: PdfStageProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [state, setState] = useState<PdfRenderState>({ kind: 'idle' });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    let cancelled = false;
    setState({ kind: 'rendering' });
    renderPdfPageToCanvas(file, page, canvas)
      .then((result) => {
        if (cancelled) return;
        setState({
          kind: 'ready',
          numPages: result.numPages,
          renderedPage: result.renderedPage,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setState({ kind: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, [file, page]);

  if (state.kind === 'error') {
    return (
      <div className="extraction-panel__empty">
        <p>PDF preview unavailable — the file could not be rendered.</p>
      </div>
    );
  }

  const renderedPage = state.kind === 'ready' ? state.renderedPage : page;
  const numPages = state.kind === 'ready' ? state.numPages : null;

  return (
    <>
      <div className="extraction-panel__source-stage">
        <canvas
          ref={canvasRef}
          data-testid="source-pdf-canvas"
          className="extraction-panel__source-canvas"
        />
        <div
          className="extraction-panel__source-overlay"
          aria-hidden="true"
          data-page={renderedPage}
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
        Page {renderedPage}
        {numPages !== null ? ` of ${numPages}` : ''}
        {selectedBbox ? ` · highlighting ${selectedBbox.field_path}` : ''}
      </p>
    </>
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
