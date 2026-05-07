/**
 * Tests for ``planUploadHandoff`` — the application-shell decision helper
 * for whether a canonical upload outcome should populate the extraction
 * panel and inject a synthetic chat turn (issue 025).
 */

import { describe, expect, it } from 'vitest';
import type { DocType, ExtractionResponse, UploadStatus } from '../api/extraction';
import { planUploadHandoff } from '../api/uploadHandoff';

function makeOutcome(overrides: Partial<ExtractionResponse>): ExtractionResponse {
  const docType: DocType = overrides.doc_type ?? 'lab_pdf';
  const status: UploadStatus = overrides.status ?? 'ok';
  const isOk = status === 'ok';
  return {
    status,
    requested_type: overrides.requested_type ?? docType,
    effective_type: isOk ? overrides.effective_type ?? docType : null,
    discussable: isOk,
    failure_reason: isOk ? null : 'something went wrong',
    document_id: 'doc-1',
    document_reference: 'DocumentReference/doc-1',
    doc_type: docType,
    filename: overrides.filename ?? 'labs.pdf',
    lab: null,
    intake: null,
    ...overrides,
  };
}

describe('planUploadHandoff', () => {
  it('returns render-and-discuss for a successful lab upload', () => {
    const plan = planUploadHandoff(
      makeOutcome({ doc_type: 'lab_pdf', filename: 'cbc.pdf' }),
    );
    expect(plan.kind).toBe('render-and-discuss');
    if (plan.kind === 'render-and-discuss') {
      expect(plan.extraction.filename).toBe('cbc.pdf');
      expect(plan.promptText).toContain('cbc.pdf');
      expect(plan.promptText).toMatch(/walk me through what's notable/i);
    }
  });

  it('returns render-and-discuss for a successful intake upload with intake-shaped prompt', () => {
    const plan = planUploadHandoff(
      makeOutcome({ doc_type: 'intake_form', filename: 'intake.pdf' }),
    );
    expect(plan.kind).toBe('render-and-discuss');
    if (plan.kind === 'render-and-discuss') {
      expect(plan.promptText).toMatch(/summarize the intake form/i);
      expect(plan.promptText).not.toMatch(/walk me through/i);
    }
  });

  it('keys the prompt off effective_type, not requested_type, when they differ', () => {
    // Belt-and-suspenders: even though the wire today emits effective ===
    // requested for ok responses, the helper should always trust the
    // effective type so future override flows keep the panel and chat
    // aligned to what the extractor actually produced.
    const plan = planUploadHandoff(
      makeOutcome({
        requested_type: 'lab_pdf',
        effective_type: 'intake_form',
        doc_type: 'intake_form',
      }),
    );
    expect(plan.kind).toBe('render-and-discuss');
    if (plan.kind === 'render-and-discuss') {
      expect(plan.promptText).toMatch(/summarize the intake form/i);
    }
  });

  it('suppresses the handoff when status is upload_failed', () => {
    const plan = planUploadHandoff(
      makeOutcome({ status: 'upload_failed' }),
    );
    expect(plan).toEqual({ kind: 'suppress', reason: 'not-ok' });
  });

  it('suppresses the handoff when status is doc_ref_failed', () => {
    const plan = planUploadHandoff(
      makeOutcome({ status: 'doc_ref_failed' }),
    );
    expect(plan).toEqual({ kind: 'suppress', reason: 'not-ok' });
  });

  it('suppresses the handoff when status is extraction_failed', () => {
    const plan = planUploadHandoff(
      makeOutcome({ status: 'extraction_failed' }),
    );
    expect(plan).toEqual({ kind: 'suppress', reason: 'not-ok' });
  });

  it('suppresses the handoff when status is unauthorized', () => {
    const plan = planUploadHandoff(makeOutcome({ status: 'unauthorized' }));
    expect(plan).toEqual({ kind: 'suppress', reason: 'not-ok' });
  });

  it('suppresses when status is ok but discussable is false', () => {
    // Defense-in-depth — a canonical envelope where status==='ok' but the
    // server explicitly marked discussable=false (e.g. a future "extracted
    // but flagged for review" state) must not produce a chat handoff.
    const plan = planUploadHandoff(
      makeOutcome({ status: 'ok', discussable: false }),
    );
    expect(plan).toEqual({ kind: 'suppress', reason: 'not-discussable' });
  });
});
