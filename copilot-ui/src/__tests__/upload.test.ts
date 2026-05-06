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

    const bad = makeFile({ name: 'scan.docx', type: '' });
    expect(validateFileForUpload(bad)).toMatchObject({ code: 'invalid_type' });
  });
});

describe('uploadDocument', () => {
  it('POSTs multipart form with file + patient_id + doc_type', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          document_id: 'doc-123',
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
          document_id: 'doc-9',
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
