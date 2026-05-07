import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExtractionResultsPanel } from '../components/ExtractionResultsPanel';
import type {
  ExtractionResponse,
  UploadBboxRecord,
} from '../api/extraction';

function makeFile(opts: { name?: string; type?: string; size?: number } = {}): File {
  const { name = 'lab.png', type = 'image/png', size = 1024 } = opts;
  const buf = new Uint8Array(size);
  return new File([buf], name, { type });
}

function bbox(
  field_path: string,
  overrides: Partial<UploadBboxRecord> = {},
): UploadBboxRecord {
  return {
    field_path,
    extracted_value: overrides.extracted_value ?? 'value',
    matched_text: overrides.matched_text ?? 'value',
    bbox: overrides.bbox ?? { page: 1, x: 0.1, y: 0.2, width: 0.3, height: 0.05 },
    match_confidence: overrides.match_confidence ?? 0.9,
  };
}

function labFixture(): ExtractionResponse {
  return {
    status: 'ok',
    requested_type: 'lab_pdf',
    effective_type: 'lab_pdf',
    discussable: true,
    failure_reason: null,
    document_id: 'doc-1',
    document_reference: 'DocumentReference/doc-1',
    doc_type: 'lab_pdf',
    filename: 'cbc.pdf',
    intake: null,
    bboxes: [],
    lab: {
      patient_name: 'Eduardo Perez',
      collection_date: '2026-04-30',
      lab_name: 'LabCorp',
      ordering_provider: 'Dr. Smith',
      results: [
        {
          test_name: 'Hemoglobin A1c',
          value: '8.2',
          unit: '%',
          reference_range: '<5.7',
          abnormal_flag: 'high',
          confidence: 'high',
        },
        {
          test_name: 'Creatinine',
          value: '0.9',
          unit: 'mg/dL',
          reference_range: '0.6 - 1.3',
          abnormal_flag: 'normal',
          confidence: 'medium',
        },
        {
          test_name: 'Potassium',
          value: '5.8',
          unit: 'mmol/L',
          reference_range: '3.5 - 5.0',
          abnormal_flag: 'critical',
          confidence: 'low',
        },
      ],
    },
  };
}

function intakeFixture(): ExtractionResponse {
  return {
    status: 'ok',
    requested_type: 'intake_form',
    effective_type: 'intake_form',
    discussable: true,
    failure_reason: null,
    document_id: 'doc-2',
    document_reference: 'DocumentReference/doc-2',
    doc_type: 'intake_form',
    filename: 'intake.pdf',
    lab: null,
    bboxes: [],
    intake: {
      demographics: {
        name: 'Maria Chen',
        date_of_birth: '1972-08-14',
        sex: 'F',
        phone: '555-0123',
        address: '101 Main St',
      },
      chief_concern: 'Headache for 3 days',
      current_medications: [
        {
          name: 'Lisinopril',
          dose: '20 mg',
          frequency: 'once daily',
          confidence: 'high',
        },
      ],
      allergies: [
        {
          substance: 'Penicillin',
          reaction: 'rash',
          severity: 'moderate',
          confidence: 'medium',
        },
      ],
      family_history: [
        { relation: 'Mother', condition: 'Type 2 Diabetes', confidence: 'high' },
      ],
      social_history: {
        tobacco: 'Never',
        alcohol: 'Occasional',
        substance_use: null,
        occupation: 'Teacher',
      },
    },
  };
}

describe('ExtractionResultsPanel', () => {
  it('renders nothing when extraction is null', () => {
    const { container } = render(<ExtractionResultsPanel extraction={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders lab results with confidence badges and abnormal flags', () => {
    render(<ExtractionResultsPanel extraction={labFixture()} />);

    expect(screen.getByText('Lab results')).toBeInTheDocument();
    expect(screen.getByText('cbc.pdf')).toBeInTheDocument();
    expect(screen.getByText('Hemoglobin A1c')).toBeInTheDocument();
    expect(screen.getByText('8.2 %')).toBeInTheDocument();
    expect(screen.getByText('<5.7')).toBeInTheDocument();
    expect(screen.getByText('Creatinine')).toBeInTheDocument();

    // Confidence badges
    const badges = screen.getAllByLabelText(/^confidence /);
    expect(badges.length).toBe(3);
    expect(badges[0]).toHaveAttribute('data-confidence', 'high');
    expect(badges[1]).toHaveAttribute('data-confidence', 'medium');
    expect(badges[2]).toHaveAttribute('data-confidence', 'low');

    // Abnormal flag classes
    const high = screen.getByText('8.2 %');
    expect(high).toHaveAttribute('data-flag', 'high');
    const critical = screen.getByText('5.8 mmol/L');
    expect(critical).toHaveAttribute('data-flag', 'critical');
  });

  it('shows an empty-state when lab has no results', () => {
    const fixture = labFixture();
    const empty: ExtractionResponse = {
      ...fixture,
      lab: { ...fixture.lab!, results: [] },
    };
    render(<ExtractionResultsPanel extraction={empty} />);
    expect(screen.getByText(/No values found/i)).toBeInTheDocument();
  });

  it('renders intake form sections (demographics, medications, allergies)', () => {
    render(<ExtractionResultsPanel extraction={intakeFixture()} />);

    expect(screen.getByText('Intake form')).toBeInTheDocument();
    // Chief concern + Demographics open by default.
    expect(screen.getByText(/Headache for 3 days/i)).toBeInTheDocument();
    expect(screen.getByText('Maria Chen')).toBeInTheDocument();
    expect(screen.getByText('1972-08-14')).toBeInTheDocument();
  });

  it('expands collapsible medication & allergy sections on click', async () => {
    render(<ExtractionResultsPanel extraction={intakeFixture()} />);

    // Medications collapsed by default — content not visible.
    expect(screen.queryByText('Lisinopril')).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: /Medications \(1\)/ }),
    );
    expect(screen.getByText('Lisinopril')).toBeInTheDocument();
    expect(screen.getByText(/20 mg · once daily/)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: /Allergies \(1\)/ }),
    );
    expect(screen.getByText('Penicillin')).toBeInTheDocument();
    expect(screen.getByText(/rash · moderate/)).toBeInTheDocument();
  });

  it('invokes onDismiss when the close button is clicked', async () => {
    const onDismiss = vi.fn();
    render(
      <ExtractionResultsPanel
        extraction={labFixture()}
        onDismiss={onDismiss}
      />,
    );

    await userEvent.click(
      screen.getByRole('button', { name: /dismiss extraction/i }),
    );
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('shows an empty-state when doc_type is intake_form but intake is null', () => {
    const fixture: ExtractionResponse = {
      status: 'ok',
      requested_type: 'intake_form',
      effective_type: 'intake_form',
      discussable: true,
      failure_reason: null,
      document_id: 'doc-x',
      document_reference: 'DocumentReference/doc-x',
      doc_type: 'intake_form',
      filename: 'broken.pdf',
      lab: null,
      intake: null,
      bboxes: [],
    };
    render(<ExtractionResultsPanel extraction={fixture} />);
    expect(
      screen.getByText(/No intake form fields were extracted/i),
    ).toBeInTheDocument();
  });

  describe('Source tabs (issue 032)', () => {
    const objectUrl = 'blob:mock-source-url';

    beforeEach(() => {
      // jsdom does not implement URL.createObjectURL.
      Object.defineProperty(URL, 'createObjectURL', {
        configurable: true,
        value: vi.fn(() => objectUrl),
      });
      Object.defineProperty(URL, 'revokeObjectURL', {
        configurable: true,
        value: vi.fn(),
      });
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('renders Results and Source tab controls when an extraction is shown', () => {
      render(<ExtractionResultsPanel extraction={labFixture()} />);
      expect(
        screen.getByRole('tab', { name: /results/i }),
      ).toHaveAttribute('aria-selected', 'true');
      expect(
        screen.getByRole('tab', { name: /source/i }),
      ).toHaveAttribute('aria-selected', 'false');
    });

    it('switches to the Source tab when the user clicks it', async () => {
      const fixture = labFixture();
      render(
        <ExtractionResultsPanel
          extraction={fixture}
          sourceFile={makeFile({ name: 'cbc.png', type: 'image/png' })}
        />,
      );
      await userEvent.click(screen.getByRole('tab', { name: /source/i }));
      expect(
        screen.getByRole('tab', { name: /source/i }),
      ).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByTestId('source-tab-panel')).toBeInTheDocument();
      expect(screen.queryByText('Hemoglobin A1c')).not.toBeInTheDocument();
    });

    it('exposes a source CTA only when an exact field_path match exists', () => {
      const fixture = labFixture();
      const withBboxes: ExtractionResponse = {
        ...fixture,
        bboxes: [bbox('results[0].value', { extracted_value: '8.2' })],
      };
      render(<ExtractionResultsPanel extraction={withBboxes} />);
      expect(
        screen.getByTestId('source-cta-results[0].value'),
      ).toBeInTheDocument();
      // No bbox for row 1 (Creatinine) → no CTA.
      expect(
        screen.queryByTestId('source-cta-results[1].value'),
      ).not.toBeInTheDocument();
    });

    it('renders no source CTAs when bboxes is empty', () => {
      render(<ExtractionResultsPanel extraction={labFixture()} />);
      const rowCount = screen.getAllByRole('row').length;
      expect(rowCount).toBeGreaterThan(0);
      expect(
        screen.queryAllByRole('button', { name: /show source/i }),
      ).toHaveLength(0);
    });

    it('selecting a source CTA switches to Source tab and marks the matching bbox', async () => {
      const fixture = labFixture();
      const sourceFile = makeFile({ name: 'cbc.png', type: 'image/png' });
      const withBboxes: ExtractionResponse = {
        ...fixture,
        bboxes: [
          bbox('results[0].value', {
            bbox: { page: 1, x: 0.1, y: 0.2, width: 0.3, height: 0.05 },
          }),
          bbox('results[1].value', {
            bbox: { page: 1, x: 0.5, y: 0.4, width: 0.2, height: 0.05 },
          }),
        ],
      };
      render(
        <ExtractionResultsPanel
          extraction={withBboxes}
          sourceFile={sourceFile}
        />,
      );
      await userEvent.click(
        screen.getByTestId('source-cta-results[0].value'),
      );
      expect(
        screen.getByRole('tab', { name: /source/i }),
      ).toHaveAttribute('aria-selected', 'true');

      const selected = screen.getByTestId('source-bbox-selected');
      expect(selected).toHaveAttribute(
        'data-field-path',
        'results[0].value',
      );

      // Both boxes render — selected prominent, others faint.
      const allBoxes = screen.getAllByTestId(/^source-bbox-/);
      expect(allBoxes.length).toBe(2);
    });

    it('renders the uploaded image in the Source tab using a browser-local object URL', async () => {
      const fixture = labFixture();
      const sourceFile = makeFile({ name: 'cbc.png', type: 'image/png' });
      const withBboxes: ExtractionResponse = {
        ...fixture,
        bboxes: [bbox('results[0].value')],
      };
      render(
        <ExtractionResultsPanel
          extraction={withBboxes}
          sourceFile={sourceFile}
        />,
      );
      await userEvent.click(screen.getByRole('tab', { name: /source/i }));
      const img = screen.getByAltText(/source preview/i) as HTMLImageElement;
      expect(img).toBeInTheDocument();
      expect(img.src).toBe(objectUrl);
    });

    it('positions bbox overlays using normalized coordinates', async () => {
      const fixture = labFixture();
      const sourceFile = makeFile({ name: 'cbc.png', type: 'image/png' });
      const withBboxes: ExtractionResponse = {
        ...fixture,
        bboxes: [
          bbox('results[0].value', {
            bbox: { page: 1, x: 0.1, y: 0.2, width: 0.3, height: 0.05 },
          }),
        ],
      };
      render(
        <ExtractionResultsPanel
          extraction={withBboxes}
          sourceFile={sourceFile}
        />,
      );
      await userEvent.click(screen.getByRole('tab', { name: /source/i }));
      const overlay = screen.getByTestId(
        'source-bbox-results[0].value',
      );
      expect(overlay.style.left).toBe('10%');
      expect(overlay.style.top).toBe('20%');
      expect(overlay.style.width).toBe('30%');
      expect(overlay.style.height).toBe('5%');
    });

    it('renders an empty-state in the Source tab when no file is provided', async () => {
      render(<ExtractionResultsPanel extraction={labFixture()} />);
      await userEvent.click(screen.getByRole('tab', { name: /source/i }));
      expect(
        screen.getByText(/no source preview available/i),
      ).toBeInTheDocument();
    });

    it('exposes a source CTA on intake chief_concern when matched', () => {
      const fixture = intakeFixture();
      const withBboxes: ExtractionResponse = {
        ...fixture,
        bboxes: [bbox('chief_concern')],
      };
      render(<ExtractionResultsPanel extraction={withBboxes} />);
      expect(
        screen.getByTestId('source-cta-chief_concern'),
      ).toBeInTheDocument();
    });
  });

  it('renders nothing when the canonical status is not ok (issue 025)', () => {
    const fixture: ExtractionResponse = {
      status: 'extraction_failed',
      requested_type: 'lab_pdf',
      effective_type: null,
      discussable: false,
      failure_reason: "We couldn't extract structured data from this document.",
      document_id: 'doc-broken',
      document_reference: 'DocumentReference/doc-broken',
      doc_type: 'lab_pdf',
      filename: 'broken.pdf',
      lab: null,
      intake: null,
      bboxes: [],
    };
    const { container } = render(
      <ExtractionResultsPanel extraction={fixture} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
