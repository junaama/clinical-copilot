import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FileUploadWidget } from '../components/FileUploadWidget';
import type { ExtractionResponse } from '../api/extraction';
import type { DocTypeMismatch } from '../api/upload';

function makeFile(opts: { name?: string; type?: string; size?: number }): File {
  const { name = 'lab.pdf', type = 'application/pdf', size = 1024 } = opts;
  const buf = new Uint8Array(size > 0 ? size : 0);
  return new File([buf], name, { type });
}

const SAMPLE_RESPONSE: ExtractionResponse = {
  status: 'ok',
  requested_type: 'lab_pdf',
  effective_type: 'lab_pdf',
  discussable: true,
  failure_reason: null,
  document_id: 'doc-1',
  document_reference: 'DocumentReference/doc-1',
  doc_type: 'lab_pdf',
  filename: 'labs.pdf',
  lab: null,
  intake: null,
  bboxes: [],
};

describe('FileUploadWidget', () => {
  it('renders disabled with a hint when patientId is empty', () => {
    const { getByTestId, getByText } = render(
      <FileUploadWidget
        patientId=""
        patientName="—"
        onUploaded={() => {}}
      />,
    );
    expect(getByTestId('upload-widget')).toBeDefined();
    expect(getByText('select a patient first')).toBeDefined();
    expect(getByText('Select a patient to enable upload')).toBeDefined();
  });

  it('uploads a valid file via the file picker and calls onUploaded', async () => {
    const uploadFn = vi.fn().mockResolvedValue({ ok: true, response: SAMPLE_RESPONSE });
    const onUploaded = vi.fn();

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="Eduardo Perez"
        onUploaded={onUploaded}
        uploadFn={uploadFn}
      />,
    );

    const input = screen.getByLabelText('choose document') as HTMLInputElement;
    await userEvent.upload(input, makeFile({ name: 'labs.pdf' }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1));
    expect(onUploaded.mock.calls[0]?.[0]).toMatchObject({ document_id: 'doc-1' });
    expect(uploadFn).toHaveBeenCalledTimes(1);
    const passedArgs = uploadFn.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(passedArgs['patientId']).toBe('pat-1');
    expect(passedArgs['docType']).toBe('lab_pdf');
  });

  it('shows a validation error for unsupported types and never calls upload', async () => {
    const uploadFn = vi.fn();
    const onUploaded = vi.fn();

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="Eduardo Perez"
        onUploaded={onUploaded}
        uploadFn={uploadFn}
      />,
    );

    const input = screen.getByLabelText('choose document') as HTMLInputElement;
    await userEvent.upload(
      input,
      makeFile({ name: 'note.txt', type: 'text/plain' }),
      // The input has `accept` set; bypass the picker filter so we can
      // verify our own client-side validation rejects this file.
      { applyAccept: false },
    );

    expect(
      await screen.findByText(/Only PDF, PNG, and JPEG/i),
    ).toBeInTheDocument();
    expect(uploadFn).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('renders user-safe failure_reason for canonical-failure outcome (issue 025)', async () => {
    const uploadFn = vi.fn().mockResolvedValue({
      ok: 'failed',
      status: 200,
      outcome: {
        status: 'extraction_failed',
        requested_type: 'lab_pdf',
        effective_type: null,
        discussable: false,
        failure_reason:
          "We couldn't extract structured data from this document. Please retry or check the file.",
        document_id: 'doc-x',
        document_reference: 'DocumentReference/doc-x',
        doc_type: 'lab_pdf',
        filename: 'broken.pdf',
        lab: null,
        intake: null,
        bboxes: [],
      },
    });
    const onUploaded = vi.fn();

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={onUploaded}
        uploadFn={uploadFn}
      />,
    );

    await userEvent.upload(
      screen.getByLabelText('choose document') as HTMLInputElement,
      makeFile({ name: 'broken.pdf' }),
    );

    const failBlock = await screen.findByTestId('upload-widget-outcome-failed');
    expect(failBlock).toHaveTextContent(/couldn't extract structured data/i);
    expect(failBlock.getAttribute('data-outcome-status')).toBe(
      'extraction_failed',
    );
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('shows a server error when upload returns ok:false', async () => {
    const uploadFn = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      detail: 'extraction service unavailable',
    });
    const onUploaded = vi.fn();

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="Eduardo Perez"
        onUploaded={onUploaded}
        uploadFn={uploadFn}
      />,
    );

    const input = screen.getByLabelText('choose document') as HTMLInputElement;
    await userEvent.upload(input, makeFile({}));

    expect(
      await screen.findByText(/extraction service unavailable/i),
    ).toBeInTheDocument();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('switches doc_type when the radio toggle is selected', async () => {
    const uploadFn = vi.fn().mockResolvedValue({ ok: true, response: SAMPLE_RESPONSE });

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={() => {}}
        uploadFn={uploadFn}
      />,
    );

    await userEvent.click(screen.getByLabelText(/intake form/i));
    const input = screen.getByLabelText('choose document') as HTMLInputElement;
    await userEvent.upload(input, makeFile({}));

    await waitFor(() => expect(uploadFn).toHaveBeenCalled());
    expect(uploadFn.mock.calls[0]?.[0]).toMatchObject({ docType: 'intake_form' });
  });

  it('shows the active document type prominently before a file is picked', () => {
    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="Eduardo Perez"
        onUploaded={() => {}}
      />,
    );
    const indicator = screen.getByTestId('upload-widget-active-type');
    expect(indicator).toHaveTextContent(/Selected document type/i);
    expect(indicator).toHaveTextContent(/Lab PDF/i);
  });

  it('updates the prominent active-type label when the radio changes', async () => {
    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={() => {}}
      />,
    );
    await userEvent.click(screen.getByLabelText(/intake form/i));
    expect(screen.getByTestId('upload-widget-active-type')).toHaveTextContent(
      /Intake form/i,
    );
  });

  it('runs the same upload path for drag-and-drop as for the file picker', async () => {
    const uploadFn = vi.fn().mockResolvedValue({ ok: true, response: SAMPLE_RESPONSE });
    const onUploaded = vi.fn();
    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={onUploaded}
        uploadFn={uploadFn}
      />,
    );
    const drop = screen.getByLabelText('drop file or click to choose');
    const file = makeFile({ name: 'labs.pdf' });
    fireEvent.drop(drop, {
      dataTransfer: { files: [file] },
    });
    await waitFor(() => expect(uploadFn).toHaveBeenCalledTimes(1));
    expect(onUploaded).toHaveBeenCalledTimes(1);
  });

  it('renders mismatch UI on 409 and offers switch / confirm / cancel', async () => {
    const mismatch: DocTypeMismatch = {
      code: 'doc_type_mismatch',
      message: 'This file looks like an intake form, not a lab PDF.',
      requestedType: 'lab_pdf',
      detectedType: 'intake_form',
      confidence: 'high',
      evidence: ["text contains 'patient demographics'"],
    };
    const uploadFn = vi.fn().mockResolvedValueOnce({
      ok: 'mismatch',
      status: 409,
      mismatch,
    });
    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={() => {}}
        uploadFn={uploadFn}
      />,
    );
    const input = screen.getByLabelText('choose document') as HTMLInputElement;
    await userEvent.upload(input, makeFile({ name: 'intake.pdf' }));
    expect(await screen.findByTestId('upload-widget-mismatch')).toBeInTheDocument();
    expect(screen.getByTestId('upload-mismatch-switch')).toBeInTheDocument();
    expect(screen.getByTestId('upload-mismatch-confirm')).toBeInTheDocument();
    expect(screen.getByTestId('upload-mismatch-cancel')).toBeInTheDocument();
  });

  it('switching type retries upload with the detected type', async () => {
    const mismatch: DocTypeMismatch = {
      code: 'doc_type_mismatch',
      message: 'This file looks like an intake form.',
      requestedType: 'lab_pdf',
      detectedType: 'intake_form',
      confidence: 'high',
      evidence: [],
    };
    const onUploaded = vi.fn();
    const uploadFn = vi
      .fn()
      .mockResolvedValueOnce({ ok: 'mismatch', status: 409, mismatch })
      .mockResolvedValueOnce({
        ok: true,
        response: { ...SAMPLE_RESPONSE, doc_type: 'intake_form' },
      });

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={onUploaded}
        uploadFn={uploadFn}
      />,
    );
    await userEvent.upload(
      screen.getByLabelText('choose document') as HTMLInputElement,
      makeFile({ name: 'intake.pdf' }),
    );
    await screen.findByTestId('upload-widget-mismatch');
    await userEvent.click(screen.getByTestId('upload-mismatch-switch'));
    await waitFor(() => expect(uploadFn).toHaveBeenCalledTimes(2));
    const secondCall = uploadFn.mock.calls[1]?.[0] as Record<string, unknown>;
    expect(secondCall['docType']).toBe('intake_form');
    expect(secondCall['confirmDocType']).toBe(false);
    expect(onUploaded).toHaveBeenCalledTimes(1);
  });

  it('confirm-anyway retries with confirmDocType=true and the originally selected type', async () => {
    const mismatch: DocTypeMismatch = {
      code: 'doc_type_mismatch',
      message: '',
      requestedType: 'lab_pdf',
      detectedType: 'intake_form',
      confidence: 'high',
      evidence: [],
    };
    const uploadFn = vi
      .fn()
      .mockResolvedValueOnce({ ok: 'mismatch', status: 409, mismatch })
      .mockResolvedValueOnce({ ok: true, response: SAMPLE_RESPONSE });

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={() => {}}
        uploadFn={uploadFn}
      />,
    );
    await userEvent.upload(
      screen.getByLabelText('choose document') as HTMLInputElement,
      makeFile({ name: 'intake.pdf' }),
    );
    await screen.findByTestId('upload-widget-mismatch');
    await userEvent.click(screen.getByTestId('upload-mismatch-confirm'));
    await waitFor(() => expect(uploadFn).toHaveBeenCalledTimes(2));
    const secondCall = uploadFn.mock.calls[1]?.[0] as Record<string, unknown>;
    expect(secondCall['docType']).toBe('lab_pdf');
    expect(secondCall['confirmDocType']).toBe(true);
  });

  it('cancel returns to idle state without retrying', async () => {
    const mismatch: DocTypeMismatch = {
      code: 'doc_type_mismatch',
      message: '',
      requestedType: 'lab_pdf',
      detectedType: 'intake_form',
      confidence: 'high',
      evidence: [],
    };
    const uploadFn = vi
      .fn()
      .mockResolvedValueOnce({ ok: 'mismatch', status: 409, mismatch });

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={() => {}}
        uploadFn={uploadFn}
      />,
    );
    await userEvent.upload(
      screen.getByLabelText('choose document') as HTMLInputElement,
      makeFile({ name: 'intake.pdf' }),
    );
    await screen.findByTestId('upload-widget-mismatch');
    await userEvent.click(screen.getByTestId('upload-mismatch-cancel'));
    await waitFor(() =>
      expect(screen.queryByTestId('upload-widget-mismatch')).not.toBeInTheDocument(),
    );
    expect(uploadFn).toHaveBeenCalledTimes(1);
  });

  it('keyboard users can resolve the mismatch via Tab + Enter', async () => {
    const mismatch: DocTypeMismatch = {
      code: 'doc_type_mismatch',
      message: '',
      requestedType: 'lab_pdf',
      detectedType: 'intake_form',
      confidence: 'high',
      evidence: [],
    };
    const uploadFn = vi
      .fn()
      .mockResolvedValueOnce({ ok: 'mismatch', status: 409, mismatch })
      .mockResolvedValueOnce({ ok: true, response: SAMPLE_RESPONSE });

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={() => {}}
        uploadFn={uploadFn}
      />,
    );
    await userEvent.upload(
      screen.getByLabelText('choose document') as HTMLInputElement,
      makeFile({ name: 'intake.pdf' }),
    );
    const switchBtn = await screen.findByTestId('upload-mismatch-switch');
    switchBtn.focus();
    expect(switchBtn).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    await waitFor(() => expect(uploadFn).toHaveBeenCalledTimes(2));
  });

  it('shows an "Uploading…" state while the upload is in flight', async () => {
    type Resolver = (v: unknown) => void;
    const deferred: { resolve: Resolver | null } = { resolve: null };
    const uploadFn = vi.fn().mockReturnValue(
      new Promise<unknown>((r) => {
        deferred.resolve = r;
      }),
    );

    render(
      <FileUploadWidget
        patientId="pat-1"
        patientName="—"
        onUploaded={() => {}}
        uploadFn={uploadFn as unknown as typeof import('../api/upload').uploadDocument}
      />,
    );

    const input = screen.getByLabelText('choose document') as HTMLInputElement;
    await userEvent.upload(input, makeFile({ name: 'labs.pdf' }));

    expect(await screen.findByText(/Uploading labs\.pdf/i)).toBeInTheDocument();

    deferred.resolve?.({ ok: true, response: SAMPLE_RESPONSE });
    await waitFor(() =>
      expect(screen.queryByText(/Uploading labs\.pdf/i)).not.toBeInTheDocument(),
    );
  });
});
