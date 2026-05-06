/**
 * POST /upload — agent's document upload + extraction endpoint (issue 011).
 *
 * Sends a multipart/form-data request with the selected file plus
 * patient_id and doc_type fields. Always resolves; never rejects. The
 * caller renders any error envelope inside the upload widget itself.
 */

import { resolveAgentUrl } from './client';
import type { DocType, ExtractionResponse } from './extraction';

export const ALLOWED_MIME_TYPES: readonly string[] = [
  'application/pdf',
  'image/png',
  'image/jpeg',
];

export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024; // 20 MB

export type UploadResult =
  | { readonly ok: true; readonly response: ExtractionResponse }
  | { readonly ok: false; readonly status: number; readonly detail: string };

export interface UploadOptions {
  readonly file: File;
  readonly patientId: string;
  readonly docType: DocType;
  readonly conversationId?: string;
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
      detail: 'Only PDF, PNG, and JPEG files are supported.',
    };
  }
  if (!file.type) {
    const lower = file.name.toLowerCase();
    const ok = lower.endsWith('.pdf') || lower.endsWith('.png') ||
      lower.endsWith('.jpg') || lower.endsWith('.jpeg');
    if (!ok) {
      return {
        code: 'invalid_type',
        detail: 'Only PDF, PNG, and JPEG files are supported.',
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
  return { ok: true, response: parsed };
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
  return (
    typeof obj['document_id'] === 'string' &&
    (obj['doc_type'] === 'lab_pdf' || obj['doc_type'] === 'intake_form') &&
    typeof obj['filename'] === 'string'
  );
}
