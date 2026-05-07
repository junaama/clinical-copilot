import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ExtractionResultsPanel } from '../components/ExtractionResultsPanel';
import type { ExtractionResponse } from '../api/extraction';

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
