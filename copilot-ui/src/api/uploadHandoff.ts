/**
 * Post-upload handoff decision helper (issue 025).
 *
 * The application shell calls this with the canonical ``ExtractionResponse``
 * returned by ``POST /upload`` to decide whether the extraction panel should
 * render *and* whether a synthetic chat turn should be injected so the agent
 * can discuss the document.
 *
 * The two are coupled by design: a successful panel result must never coexist
 * with a chat turn that says the same document cannot be read. The shape of
 * this helper's result enforces that — the only branch that produces a chat
 * prompt also produces the extraction to render.
 */

import type { DocType, ExtractionResponse } from './extraction';

function _buildPromptText(docType: DocType, filename: string): string {
  switch (docType) {
    case 'lab_pdf':
    case 'hl7_oru':
      return `I just uploaded ${filename}. In short sections, tell me what changed, what I should pay attention to, and what source evidence backs it up.`;
    case 'docx_referral':
      return `I just uploaded ${filename}. Summarize the referral: who is referring, the reason, pertinent history, and requested actions.`;
    case 'xlsx_workbook':
      return `I just uploaded ${filename}. Summarize the key clinical data: patient info, medications, lab trends, and any care gaps.`;
    case 'hl7_adt':
      return `I just uploaded ${filename}. Summarize the patient registration or encounter update details.`;
    case 'tiff_fax':
      return `I just uploaded ${filename}. Summarize the fax content, noting any low-confidence values from the scan.`;
    case 'intake_form':
    default:
      return `I just uploaded ${filename}. In short sections, summarize what changed, what I should pay attention to, and what source evidence backs it up.`;
  }
}

export type UploadHandoffPlan =
  | {
      readonly kind: 'render-and-discuss';
      readonly extraction: ExtractionResponse;
      readonly promptText: string;
    }
  | {
      readonly kind: 'suppress';
      readonly reason: 'not-ok' | 'not-discussable';
    };

export function planUploadHandoff(
  response: ExtractionResponse,
): UploadHandoffPlan {
  if (response.status !== 'ok') {
    return { kind: 'suppress', reason: 'not-ok' };
  }
  if (response.discussable !== true) {
    return { kind: 'suppress', reason: 'not-discussable' };
  }
  const effectiveType: DocType = response.effective_type ?? response.doc_type;
  const promptText = _buildPromptText(effectiveType, response.filename);
  return { kind: 'render-and-discuss', extraction: response, promptText };
}
