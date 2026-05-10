/**
 * POST /upload — agent's document upload + extraction endpoint (issue 011).
 *
 * Sends a multipart/form-data request with the selected file plus
 * patient_id and doc_type fields. Always resolves; never rejects. The
 * caller renders any error envelope inside the upload widget itself.
 */

import { resolveAgentUrl } from './client';
import type { DocType, ExtractionResponse } from './extraction';

export type UploadDocType = DocType | 'auto';

export const ALLOWED_MIME_TYPES: readonly string[] = [
  'application/pdf',
  'image/png',
  'image/jpeg',
  'image/tiff',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'x-application/hl7-v2+er7',
];

/** File extensions accepted by the file picker (for browser ``accept``). */
export const ALLOWED_EXTENSIONS: readonly string[] = [
  '.pdf',
  '.png',
  '.jpg',
  '.jpeg',
  '.tiff',
  '.tif',
  '.docx',
  '.xlsx',
  '.hl7',
];

export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024; // 20 MB

export interface DocTypeMismatch {
  readonly code: 'doc_type_mismatch';
  readonly message: string;
  readonly requestedType: UploadDocType;
  readonly detectedType: DocType;
  readonly confidence: 'high' | 'medium' | 'low';
  readonly evidence: readonly string[];
}

export type UploadResult =
  | { readonly ok: true; readonly response: ExtractionResponse }
  /** Canonical upload outcome with a non-ok status — surfaced as a
   * user-facing error in the widget; never injects a chat turn. */
  | {
      readonly ok: 'failed';
      readonly status: number;
      readonly outcome: ExtractionResponse;
    }
  | { readonly ok: 'mismatch'; readonly status: 409; readonly mismatch: DocTypeMismatch }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface UploadOptions {
  readonly file: File;
  readonly patientId: string;
  readonly docType: UploadDocType;
  readonly conversationId?: string;
  readonly confirmDocType?: boolean;
  readonly fetcher?: typeof fetch;
  readonly baseUrl?: string;
  readonly signal?: AbortSignal;
}

export interface ValidationError {
  readonly code: 'invalid_type' | 'too_large' | 'empty';
  readonly detail: string;
}

/** Pure client-side validation. Returns null when the file is acceptable. */
export function validateFileForUpload(file: File): ValidationError | null {
  if (file.size === 0) {
    return { code: 'empty', detail: 'File is empty.' };
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return {
      code: 'too_large',
      detail: `File exceeds the 20 MB limit (${(file.size / 1024 / 1024).toFixed(1)} MB).`,
    };
  }
  // file.type is empty string for some macOS drag-drops; fall back to extension.
  if (file.type && !ALLOWED_MIME_TYPES.includes(file.type)) {
    return {
      code: 'invalid_type',
      detail:
        'Supported formats: PDF, PNG, JPEG, TIFF, DOCX, XLSX, and HL7.',
    };
  }
  if (!file.type) {
    const lower = file.name.toLowerCase();
    const ok =
      lower.endsWith('.pdf') ||
      lower.endsWith('.png') ||
      lower.endsWith('.jpg') ||
      lower.endsWith('.jpeg') ||
      lower.endsWith('.tiff') ||
      lower.endsWith('.tif') ||
      lower.endsWith('.docx') ||
      lower.endsWith('.xlsx') ||
      lower.endsWith('.hl7');
    if (!ok) {
      return {
        code: 'invalid_type',
        detail:
          'Supported formats: PDF, PNG, JPEG, TIFF, DOCX, XLSX, and HL7.',
      };
    }
  }
  return null;
}

export async function uploadDocument(opts: UploadOptions): Promise<UploadResult> {
  const fetcher = opts.fetcher ?? fetch;
  const baseUrl = opts.baseUrl ?? resolveAgentUrl();
  const url = `${baseUrl}/upload`;

  const form = new FormData();
  form.append('file', opts.file, opts.file.name);
  form.append('patient_id', opts.patientId);
  form.append('doc_type', opts.docType);
  if (opts.conversationId) {
    form.append('conversation_id', opts.conversationId);
  }
  if (opts.confirmDocType) {
    form.append('confirm_doc_type', 'true');
  }

  let resp: Response;
  try {
    resp = await fetcher(url, {
      method: 'POST',
      body: form,
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: opts.signal,
    });
  } catch (error: unknown) {
    return {
      ok: false,
      status: 0,
      detail: error instanceof Error ? error.message : 'network error',
    };
  }

  let bodyText: string;
  try {
    bodyText = await resp.text();
  } catch {
    return { ok: false, status: resp.status, detail: 'response body unreadable' };
  }

  if (!resp.ok) {
    if (resp.status === 409) {
      const mismatch = parseMismatchDetail(bodyText);
      if (mismatch) {
        return { ok: 'mismatch', status: 409, mismatch };
      }
    }
    return { ok: false, status: resp.status, detail: extractDetail(bodyText, resp.status) };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    return { ok: false, status: resp.status, detail: 'invalid JSON in response' };
  }

  if (!isExtractionResponse(parsed)) {
    return { ok: false, status: resp.status, detail: 'unexpected response shape' };
  }
  // Canonical envelope (issue 025): ``status`` discriminates success from
  // every partial-failure mode. Only ``ok && discussable`` should drive
  // panel rendering and synthetic chat injection at the call site.
  if (parsed.status !== 'ok' || parsed.discussable !== true) {
    return { ok: 'failed', status: resp.status, outcome: parsed };
  }
  return { ok: true, response: parsed };
}

function parseMismatchDetail(bodyText: string): DocTypeMismatch | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const obj = parsed as Record<string, unknown>;
  const detail = obj['detail'];
  if (typeof detail !== 'object' || detail === null) return null;
  const d = detail as Record<string, unknown>;
  if (d['code'] !== 'doc_type_mismatch') return null;
  const requested = d['requested_type'];
  const detected = d['detected_type'];
  const confidence = d['confidence'];
  if (
    !_isValidUploadDocType(requested) ||
    !_isValidDocType(detected) ||
    (confidence !== 'high' && confidence !== 'medium' && confidence !== 'low')
  ) {
    return null;
  }
  const message = typeof d['message'] === 'string' ? d['message'] : '';
  const rawEvidence = Array.isArray(d['evidence']) ? d['evidence'] : [];
  const evidence: string[] = rawEvidence.filter(
    (s: unknown): s is string => typeof s === 'string',
  );
  return {
    code: 'doc_type_mismatch',
    message,
    requestedType: requested,
    detectedType: detected,
    confidence,
    evidence,
  };
}

function extractDetail(bodyText: string, status: number): string {
  if (bodyText.length === 0) return `HTTP ${status}`;
  try {
    const obj: unknown = JSON.parse(bodyText);
    if (typeof obj === 'object' && obj !== null && 'detail' in obj) {
      const detail = (obj as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
    }
  } catch {
    // fall through
  }
  return bodyText.slice(0, 500);
}

function isExtractionResponse(x: unknown): x is ExtractionResponse {
  if (typeof x !== 'object' || x === null) return false;
  const obj = x as Record<string, unknown>;
  const status = obj['status'];
  const validStatus =
    status === 'ok' ||
    status === 'upload_failed' ||
    status === 'doc_ref_failed' ||
    status === 'extraction_failed' ||
    status === 'persistence_failed' ||
    status === 'unauthorized';
  if (!validStatus) return false;
  if (typeof obj['discussable'] !== 'boolean') return false;
  if (!_isValidUploadDocType(obj['requested_type'])) {
    return false;
  }
  // ``bboxes`` is the issue-031 drawable-only contract: must be an array
  // when present. Older builds may omit the field; tolerate that for
  // forward compatibility with cached frontends rather than rejecting
  // an otherwise-valid envelope.
  const bboxes = obj['bboxes'];
  if (bboxes !== undefined && !Array.isArray(bboxes)) return false;
  return (
    (obj['document_id'] === null || typeof obj['document_id'] === 'string') &&
    _isValidDocType(obj['doc_type']) &&
    typeof obj['filename'] === 'string'
  );
}

function _isValidDocType(value: unknown): value is DocType {
  return (
    value === 'lab_pdf' ||
    value === 'intake_form' ||
    value === 'hl7_oru' ||
    value === 'hl7_adt' ||
    value === 'xlsx_workbook' ||
    value === 'docx_referral' ||
    value === 'tiff_fax'
  );
}

function _isValidUploadDocType(value: unknown): value is UploadDocType {
  return value === 'auto' || _isValidDocType(value);
}
