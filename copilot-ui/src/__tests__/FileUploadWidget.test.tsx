import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FileUploadWidget } from '../components/FileUploadWidget';
import type { ExtractionResponse } from '../api/extraction';

function makeFile(opts: { name?: string; type?: string; size?: number }): File {
  const { name = 'lab.pdf', type = 'application/pdf', size = 1024 } = opts;
  const buf = new Uint8Array(size > 0 ? size : 0);
  return new File([buf], name, { type });
}

const SAMPLE_RESPONSE: ExtractionResponse = {
  document_id: 'doc-1',
  doc_type: 'lab_pdf',
  filename: 'labs.pdf',
  lab: null,
  intake: null,
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
