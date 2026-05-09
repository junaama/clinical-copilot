import { describe, expect, it, vi } from 'vitest';
import {
  MAX_UPLOAD_BYTES,
  uploadDocument,
  validateFileForUpload,
} from '../api/upload';

function makeFile(opts: {
  name?: string;
  type?: string;
  size?: number;
}): File {
  const { name = 'lab.pdf', type = 'application/pdf', size = 1024 } = opts;
  // Build a Blob of the requested size; jsdom's File honors `.size`.
  const buf = new Uint8Array(size > 0 ? size : 0);
  return new File([buf], name, { type });
}

describe('validateFileForUpload', () => {
  it('accepts a valid PDF', () => {
    const file = makeFile({ name: 'labs.pdf', type: 'application/pdf' });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('accepts a valid PNG', () => {
    const file = makeFile({ name: 'scan.png', type: 'image/png' });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('accepts a valid JPEG', () => {
    const file = makeFile({ name: 'scan.jpg', type: 'image/jpeg' });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('rejects an empty file', () => {
    const file = makeFile({ size: 0 });
    expect(validateFileForUpload(file)).toMatchObject({ code: 'empty' });
  });

  it('rejects an oversized file', () => {
    const file = makeFile({ size: MAX_UPLOAD_BYTES + 1 });
    expect(validateFileForUpload(file)).toMatchObject({ code: 'too_large' });
  });

  it('rejects an unsupported MIME type', () => {
    const file = makeFile({ name: 'note.txt', type: 'text/plain' });
    expect(validateFileForUpload(file)).toMatchObject({ code: 'invalid_type' });
  });

  it('falls back to extension when MIME type is empty (drag-drop on macOS)', () => {
    const ok = makeFile({ name: 'scan.PDF', type: '' });
    expect(validateFileForUpload(ok)).toBeNull();

    // DOCX is now a supported format (week-2 multi-format upload).
    const docx = makeFile({ name: 'referral.docx', type: '' });
    expect(validateFileForUpload(docx)).toBeNull();

    const bad = makeFile({ name: 'archive.zip', type: '' });
    expect(validateFileForUpload(bad)).toMatchObject({ code: 'invalid_type' });
  });

  // Week-2 multi-format upload support
  it('accepts TIFF files', () => {
    const file = makeFile({ name: 'fax.tiff', type: 'image/tiff' });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('accepts DOCX files', () => {
    const file = makeFile({
      name: 'referral.docx',
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('accepts XLSX files', () => {
    const file = makeFile({
      name: 'workbook.xlsx',
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('accepts HL7 files by extension when MIME is empty', () => {
    const file = makeFile({ name: 'labs.hl7', type: '' });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('accepts .tif extension when MIME is empty', () => {
    const file = makeFile({ name: 'scan.tif', type: '' });
    expect(validateFileForUpload(file)).toBeNull();
  });

  it('rejects unsupported extension when MIME is empty', () => {
    const file = makeFile({ name: 'data.csv', type: '' });
    expect(validateFileForUpload(file)).toMatchObject({ code: 'invalid_type' });
  });
});

describe('uploadDocument', () => {
  it('POSTs multipart form with file + patient_id + doc_type', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          status: 'ok',
          requested_type: 'lab_pdf',
          effective_type: 'lab_pdf',
          discussable: true,
          failure_reason: null,
          document_id: 'doc-123',
          document_reference: 'DocumentReference/doc-123',
          doc_type: 'lab_pdf',
          filename: 'labs.pdf',
          lab: null,
          intake: null,
        }),
    } as Response);

    const file = makeFile({ name: 'labs.pdf' });
    const result = await uploadDocument({
      file,
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });

    expect(result.ok).toBe(true);
    if (result.ok === true) {
      expect(result.response.document_id).toBe('doc-123');
      expect(result.response.doc_type).toBe('lab_pdf');
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://test/upload');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get('patient_id')).toBe('pat-1');
    expect(form.get('doc_type')).toBe('lab_pdf');
    expect(form.get('file')).toBeInstanceOf(File);
  });

  it('returns ok:false with detail when server returns 4xx', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 413,
      text: async () => JSON.stringify({ detail: 'file too large' }),
    } as Response);
    const file = makeFile({});

    const result = await uploadDocument({
      file,
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(413);
      expect(result.detail).toBe('file too large');
    }
  });

  it('returns ok:false with status 0 on network error', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('fetch failed'));
    const file = makeFile({});

    const result = await uploadDocument({
      file,
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(0);
      expect(result.detail).toBe('fetch failed');
    }
  });

  it('returns ok:"mismatch" with structured detail on HTTP 409', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      text: async () =>
        JSON.stringify({
          detail: {
            code: 'doc_type_mismatch',
            message: 'This file looks like an intake_form.',
            requested_type: 'lab_pdf',
            detected_type: 'intake_form',
            confidence: 'high',
            evidence: ["text contains 'patient demographics'"],
          },
        }),
    } as Response);

    const result = await uploadDocument({
      file: makeFile({}),
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });

    expect(result.ok).toBe('mismatch');
    if (result.ok === 'mismatch') {
      expect(result.status).toBe(409);
      expect(result.mismatch.requestedType).toBe('lab_pdf');
      expect(result.mismatch.detectedType).toBe('intake_form');
      expect(result.mismatch.confidence).toBe('high');
      expect(result.mismatch.evidence).toEqual([
        "text contains 'patient demographics'",
      ]);
    }
  });

  it('passes confirm_doc_type=true when confirmDocType option is set', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          status: 'ok',
          requested_type: 'lab_pdf',
          effective_type: 'lab_pdf',
          discussable: true,
          failure_reason: null,
          document_id: 'doc-9',
          document_reference: 'DocumentReference/doc-9',
          doc_type: 'lab_pdf',
          filename: 'x.pdf',
          lab: null,
          intake: null,
        }),
    } as Response);

    await uploadDocument({
      file: makeFile({}),
      patientId: 'pat-1',
      docType: 'lab_pdf',
      confirmDocType: true,
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });

    const [, init] = fetcher.mock.calls[0] as [string, RequestInit];
    const form = init.body as FormData;
    expect(form.get('confirm_doc_type')).toBe('true');
  });

  it('falls back to ok:false on a 409 with non-mismatch detail', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      text: async () => JSON.stringify({ detail: 'something else' }),
    } as Response);

    const result = await uploadDocument({
      file: makeFile({}),
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });
    expect(result.ok).toBe(false);
    if (result.ok === false) {
      expect(result.status).toBe(409);
      expect(result.detail).toBe('something else');
    }
  });

  it('returns ok:"failed" with canonical outcome on extraction_failed (issue 025)', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          status: 'extraction_failed',
          requested_type: 'lab_pdf',
          effective_type: null,
          discussable: false,
          failure_reason:
            "We couldn't extract structured data from this document. Please retry or check the file.",
          document_id: 'doc-7',
          document_reference: 'DocumentReference/doc-7',
          doc_type: 'lab_pdf',
          filename: 'broken.pdf',
          lab: null,
          intake: null,
        }),
    } as Response);

    const result = await uploadDocument({
      file: makeFile({}),
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });

    expect(result.ok).toBe('failed');
    if (result.ok === 'failed') {
      expect(result.outcome.status).toBe('extraction_failed');
      expect(result.outcome.discussable).toBe(false);
      expect(result.outcome.failure_reason).toContain("couldn't extract");
    }
  });

  it('returns ok:"failed" when status is ok but discussable is false', async () => {
    // Defense-in-depth: a malformed/stale envelope where status==='ok' but
    // discussable===false must not be treated as a successful extraction.
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          status: 'ok',
          requested_type: 'lab_pdf',
          effective_type: 'lab_pdf',
          discussable: false,
          failure_reason: null,
          document_id: 'doc-x',
          document_reference: 'DocumentReference/doc-x',
          doc_type: 'lab_pdf',
          filename: 'x.pdf',
          lab: null,
          intake: null,
        }),
    } as Response);

    const result = await uploadDocument({
      file: makeFile({}),
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });
    expect(result.ok).toBe('failed');
  });

  it('rejects malformed response shape', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ random: 'junk' }),
    } as Response);
    const file = makeFile({});

    const result = await uploadDocument({
      file,
      patientId: 'pat-1',
      docType: 'lab_pdf',
      fetcher: fetcher as unknown as typeof fetch,
      baseUrl: 'http://test',
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.detail).toBe('unexpected response shape');
    }
  });
});
