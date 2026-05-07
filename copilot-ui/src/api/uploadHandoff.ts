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
  const promptText =
    effectiveType === 'lab_pdf'
      ? `I just uploaded ${response.filename}. Walk me through what's notable.`
      : `I just uploaded ${response.filename}. Summarize the intake form.`;
  return { kind: 'render-and-discuss', extraction: response, promptText };
}
